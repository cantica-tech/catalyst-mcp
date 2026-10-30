import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from catalyst_users.admin import User
from catalyst_users.errors import AdminError, NotFoundError, NotLoggedInError
from catalyst_users.provider import Whoami

from catalyst_mcp.iam import (
    CatalystUsersDirectory,
    IamConflictError,
    IamError,
    IamNotFoundError,
    Person,
    Registered,
    Role,
    catalyst_users as module,
)
from catalyst_mcp.settings import Settings

ALICE = Whoami(issuer="https://id.example.org/realms/catalyst", subject="sub-alice", username="alice", name="Alice")


class FakeDirectory:
    """catalyst-users' KeycloakAdmin, in memory; records the thread each call ran on."""

    def __init__(self, threads: list[int]) -> None:
        self.users: dict[str, User] = {}
        self.groups: dict[str, list[str]] = {}
        self.granted: list[tuple[str, str]] = []
        self.threads = threads
        self.race: User | None = None  # created by someone else between the lookup and the create
        self.updates: list[tuple[str, Any, Any, Any]] = []

    def _seen(self) -> None:
        self.threads.append(threading.get_ident())

    def user(self, username: str) -> User:
        self._seen()
        if username not in self.users:
            raise NotFoundError(f"user {username}")
        return self.users[username]

    def list_users(self, search: str | None = None, limit: int = 100) -> list[User]:
        self._seen()
        return [u for u in self.users.values() if not search or search in u.username][:limit]

    def create_user(self, username: str, **details: Any) -> User:
        self._seen()
        if self.race is not None:
            self.users[username], self.race = self.race, None
        if username in self.users:
            raise AdminError(409, f"user {username} exists")
        name = " ".join(p for p in (details.get("first_name"), details.get("last_name")) if p)
        self.users[username] = User(f"id-{username}", username, details.get("email"), name, True)
        return self.users[username]

    def set_enabled(self, username: str, enabled: bool) -> User:
        self.users[username] = replace(self.user(username), enabled=enabled)
        return self.users[username]

    def update_user(self, username: str, email=None, first_name=None, last_name=None) -> User:
        self.updates.append((username, email, first_name, last_name))
        found = self.user(username)
        name = " ".join(p for p in (first_name, last_name) if p) or found.name
        self.users[username] = replace(found, email=email if email is not None else found.email, name=name)
        return self.users[username]

    def user_groups(self, username: str) -> list[str]:
        self.user(username)
        return sorted(self.groups.get(username, []))

    def roles(self) -> list[tuple[str, str]]:
        self._seen()
        return [("developer", "Develops"), ("tech-lead", "")]

    def grant_role_to_user(self, username: str, role: str) -> None:
        self.user(username)
        if role not in {name for name, _ in self.roles()}:
            raise AdminError(404, f"not found: role {role}")
        self.granted.append((username, role))


class FakeProvider:
    kind = "keycloak"
    issuer = ALICE.issuer

    def __init__(self) -> None:
        self.threads: list[int] = []
        self.dir = FakeDirectory(self.threads)
        self.known: dict[str, list[str]] = {"catalyst-mcp": ["alice"]}
        self.logged_in = True

    def directory(self, system: bool = False) -> FakeDirectory:
        return self.dir

    def whoami(self, username: str | None = None) -> Whoami:
        self.threads.append(threading.get_ident())
        if not self.logged_in:
            raise NotLoggedInError
        return ALICE

    def system_whoami(self) -> Whoami:
        return replace(ALICE, username="system", subject="sub-system")

    def criteria(self) -> list[dict[str, Any]]:
        return [{"name": name, "admins": admins} for name, admins in self.known.items()]

    def add_criterion(self, name: str, admin: str | None = None) -> list[str]:
        if name in self.known:
            return []
        self.known[name] = [admin] if admin else []
        return [f"/criteria/{name}", f"/criteria/{name}/admins", f"/criteria/{name}/members"]

    def members(self, name: str, *, system: bool = False) -> list[str]:
        if name not in self.known:
            raise NotFoundError(f"group /criteria/{name}/members")
        return ["alice", "bob"]


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def directory(provider: FakeProvider) -> CatalystUsersDirectory:
    return CatalystUsersDirectory(lambda: provider)  # type: ignore[arg-type,return-value]


async def test_register_creates_then_is_idempotent(directory: CatalystUsersDirectory) -> None:
    first = await directory.register_user("bob", email="bob@example.org", first_name="Bob", last_name="Builder")
    assert first == Registered(Person("bob", "bob@example.org", "Bob Builder", True), created=True)
    again = await directory.register_user("bob", email="BOB@example.org", first_name="Bob", last_name="Builder")
    assert again == Registered(first.person, created=False)
    assert (await directory.register_user("bob")).created is False


async def test_register_refuses_other_details(directory: CatalystUsersDirectory) -> None:
    await directory.register_user("bob", email="bob@example.org", first_name="Bob")
    with pytest.raises(IamConflictError, match="register_user: bob exists with another email and name"):
        await directory.register_user("bob", email="robert@example.org", first_name="Robert")


async def test_register_meets_a_person_registered_meanwhile(
    directory: CatalystUsersDirectory, provider: FakeProvider
) -> None:
    provider.dir.race = User("id-bob", "bob", "bob@example.org", "Bob", True)
    assert (await directory.register_user("bob", email="bob@example.org")).created is False
    provider.dir.race = User("id-carol", "carol", "carol@example.org", "Carol", True)
    with pytest.raises(IamConflictError):
        await directory.register_user("carol", email="someone@example.org")


async def test_a_person_is_disabled_never_deleted(directory: CatalystUsersDirectory, provider: FakeProvider) -> None:
    await directory.register_user("bob")
    assert (await directory.set_enabled("bob", False)).enabled is False
    assert "bob" in provider.dir.users
    assert (await directory.set_enabled("bob", True)).enabled is True
    assert not hasattr(directory, "delete_user")


async def test_a_persons_details_change_through_the_facade(
    directory: CatalystUsersDirectory, provider: FakeProvider
) -> None:
    await directory.register_user("bob", email="bob@example.org")
    changed = await directory.update_user("bob", email="robert@example.org", first_name="Robert")
    assert (changed.email, changed.name) == ("robert@example.org", "Robert")
    assert provider.dir.updates == [("bob", "robert@example.org", "Robert", None)]
    with pytest.raises(IamNotFoundError, match="update_user: not found"):
        await directory.update_user("nobody", email="n@example.org")


async def test_people_roles_and_groups(directory: CatalystUsersDirectory, provider: FakeProvider) -> None:
    for name in ("bob", "bea", "carol"):
        await directory.register_user(name)
    provider.dir.groups["bob"] = ["/criteria/catalyst-mcp/members"]
    assert [p.username for p in await directory.list_users(search="b")] == ["bob", "bea"]
    assert [p.username for p in await directory.list_users(limit=1)] == ["bob"]
    assert (await directory.get_user("carol")).name == ""
    assert await directory.user_groups("bob") == ["/criteria/catalyst-mcp/members"]
    assert await directory.roles() == [Role("developer", "Develops"), Role("tech-lead", "")]
    await directory.grant_role("bob", "developer")
    assert provider.dir.granted == [("bob", "developer")]


async def test_criteria_are_added_idempotently_and_their_members_read(directory: CatalystUsersDirectory) -> None:
    assert await directory.add_criterion("c1", admin="alice") == [
        "/criteria/c1",
        "/criteria/c1/admins",
        "/criteria/c1/members",
    ]
    assert await directory.add_criterion("c1", admin="alice") == []
    assert {c["name"] for c in await directory.criteria()} == {"catalyst-mcp", "c1"}
    assert await directory.criterion_members("catalyst-mcp") == ["alice", "bob"]


async def test_the_identity(directory: CatalystUsersDirectory, provider: FakeProvider) -> None:
    assert (await directory.whoami()).signer()["sub"] == "sub-alice"
    assert (await directory.system_whoami()).username == "system"
    provider.logged_in = False
    with pytest.raises(IamError, match="whoami: not logged in"):
        await directory.whoami()


async def test_errors_name_the_operation(directory: CatalystUsersDirectory) -> None:
    with pytest.raises(IamNotFoundError, match="get_user: not found: user nobody") as found:
        await directory.get_user("nobody")
    assert found.value.status == 404
    assert found.value.operation == "get_user"
    await directory.register_user("bob")
    with pytest.raises(IamNotFoundError, match="grant_role: not found: role admin"):
        await directory.grant_role("bob", "admin")
    with pytest.raises(IamNotFoundError, match="criterion_members"):
        await directory.criterion_members("unknown")


async def test_every_call_runs_off_the_event_loop(directory: CatalystUsersDirectory, provider: FakeProvider) -> None:
    loop_thread = threading.get_ident()
    await directory.register_user("bob")
    await directory.get_user("bob")
    await directory.list_users()
    await directory.whoami()
    assert provider.threads
    assert loop_thread not in provider.threads


async def test_the_provider_is_loaded_once_on_first_use(provider: FakeProvider) -> None:
    loads: list[int] = []

    def load() -> FakeProvider:
        loads.append(1)
        return provider

    directory = CatalystUsersDirectory(load)  # type: ignore[arg-type]
    assert loads == []
    await directory.whoami()
    await directory.list_users()
    assert loads == [1]


def test_from_settings_passes_the_pointer_and_the_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def pointer(overrides: dict[str, Any], pointer_file: str | None = None) -> dict[str, Any]:
        seen.update(overrides=overrides, pointer_file=pointer_file)
        return {"provider": "keycloak"}

    monkeypatch.setattr(module.config, "pointer", pointer)
    monkeypatch.setattr(module, "from_pointer", lambda doc: ("provider", doc))
    settings = Settings(keycloak_url="https://id.example.org", users_pointer=Path("/etc/catalyst/pointer.json"))
    directory = CatalystUsersDirectory.from_settings(settings)
    assert seen == {}  # nothing is read before the first call
    assert directory.provider() == ("provider", {"provider": "keycloak"})
    assert seen == {"overrides": {"server_url": "https://id.example.org"}, "pointer_file": "/etc/catalyst/pointer.json"}
