import json
from dataclasses import replace
from typing import Any

import pytest
from catalyst_users.errors import NotLoggedInError, ProviderUnavailableError
from catalyst_users.provider import Whoami
from mcp.server.auth.provider import AccessToken
from pydantic import SecretStr

from catalyst_mcp import log
from catalyst_mcp.auth import (
    Action,
    Actor,
    ActorUnresolvedError,
    AuthorizationMismatchError,
    AuthorizedAction,
    Decision,
    DeniedError,
    DenyAll,
    Gate,
    PinnedResolver,
    SessionResolver,
    TokenResolver,
)
from catalyst_mcp.auth.token import CatalystUsersTokenVerifier
from catalyst_mcp.iam import IamError, IamNotLoggedInError

ISSUER = "https://id.example.org/realms/catalyst"
JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.c2lnbmF0dXJl"
ALICE = Whoami(
    issuer=ISSUER,
    subject="sub-alice",
    username="alice",
    name="Alice",
    groups=("/criteria/c1/admins",),
    roles=("developer",),
    grants=("cmd:*",),
    administers=("c1",),
)
SYSTEM = replace(ALICE, subject="sub-system", username="oliviersteck", grants=(), administers=())


class FakeDirectory:
    def __init__(self, me: Exception | Whoami = ALICE, system: Exception | Whoami = SYSTEM) -> None:
        self.me, self.system = me, system

    async def whoami(self) -> Whoami:
        if isinstance(self.me, Exception):
            raise self.me
        return self.me

    async def system_whoami(self) -> Whoami:
        if isinstance(self.system, Exception):
            raise self.system
        return self.system


class Allow:
    def __init__(self) -> None:
        self.asked: list[tuple[str, Action, str | None]] = []

    async def can(self, actor: Actor, action: Action, criterion_id: str | None) -> Decision:
        self.asked.append((actor.username, action, criterion_id))
        return Decision.allow("allowed for the test")


class Broken:
    async def can(self, actor: Actor, action: Action, criterion_id: str | None) -> Decision:
        raise RuntimeError(f"Bearer {JWT}")


class Vague:
    async def can(self, actor: Actor, action: Action, criterion_id: str | None) -> Any:
        return True


def actor(identity: Whoami = ALICE) -> Actor:
    return Actor.from_identity(identity, "session")


@pytest.fixture
def records(capsys: pytest.CaptureFixture[str]):
    log.configure("INFO", "json")

    def read() -> list[dict[str, Any]]:
        return [json.loads(line) for line in capsys.readouterr().err.splitlines() if line]

    return read


# --- resolvers ---------------------------------------------------------------------------------------------------


async def test_the_session_resolves_the_logged_in_identity() -> None:
    resolved = await SessionResolver(FakeDirectory()).resolve()  # type: ignore[arg-type]
    assert (resolved.username, resolved.via, resolved.grants) == ("alice", "session", ("cmd:*",))
    assert resolved.describe() == {"iss": ISSUER, "sub": "sub-alice", "username": "alice", "via": "session"}


async def test_with_no_one_logged_in_the_system_account_acts() -> None:
    directory = FakeDirectory(me=IamNotLoggedInError("whoami", str(NotLoggedInError())))
    resolved = await SessionResolver(directory).resolve()  # type: ignore[arg-type]
    assert (resolved.username, resolved.via) == ("oliviersteck", "system")


async def test_a_failing_session_is_unresolved_not_the_system_account() -> None:
    directory = FakeDirectory(me=IamError("whoami", "the provider could not be reached"))
    with pytest.raises(ActorUnresolvedError, match="logged-in identity could not be verified"):
        await SessionResolver(directory).resolve()  # type: ignore[arg-type]


async def test_neither_session_nor_system_account_is_unresolved() -> None:
    directory = FakeDirectory(
        me=IamNotLoggedInError("whoami", "not logged in"), system=IamError("system_whoami", "no system account")
    )
    with pytest.raises(ActorUnresolvedError, match="no one is logged in and the system account"):
        await SessionResolver(directory).resolve()  # type: ignore[arg-type]


async def test_the_token_resolver_reads_the_requests_verified_token() -> None:
    claims = {
        "iss": ISSUER,
        "sub": "sub-bob",
        "preferred_username": "bob",
        "groups": ["criteria/c1/members"],
        "realm_access": {"roles": ["developer"]},
        "resource_access": {"catalyst": {"roles": ["cmd:status"]}},
    }
    token = AccessToken(token=JWT, client_id="catalyst-cli", scopes=[], subject="sub-bob", claims=claims)
    resolved = await TokenResolver(ISSUER, "catalyst", current=lambda: token).resolve()
    assert (resolved.username, resolved.subject, resolved.via) == ("bob", "sub-bob", "token")
    assert (resolved.groups, resolved.roles, resolved.grants) == (
        ("/criteria/c1/members",),
        ("developer",),
        ("cmd:status",),
    )
    with pytest.raises(ActorUnresolvedError, match="no verified access token"):
        await TokenResolver(ISSUER, "catalyst", current=lambda: None).resolve()


async def test_a_watch_acts_only_as_the_identity_that_started_it() -> None:
    started_by = actor()
    same = PinnedResolver(SessionResolver(FakeDirectory(me=replace(ALICE, username="alice2"))), started_by)  # type: ignore[arg-type]
    assert (await same.resolve()).username == "alice2"  # renamed, still the same person
    other = PinnedResolver(SessionResolver(FakeDirectory(me=replace(ALICE, subject="sub-mallory"))), started_by)  # type: ignore[arg-type]
    with pytest.raises(ActorUnresolvedError, match=r"started this watch \(alice\) is no longer the one verified"):
        await other.resolve()


# --- the gate ----------------------------------------------------------------------------------------------------


async def test_by_default_every_write_is_refused_and_logged(records) -> None:
    gate = Gate(DenyAll())
    with pytest.raises(DeniedError, match=r"refused: criterion\.update on criterion c1: no permissions are configured"):
        await gate.authorize(actor(), Action.CRITERION_UPDATE, "c1")
    with pytest.raises(DeniedError, match=r"refused: user\.register on the realm"):
        await gate.authorize(actor(), Action.USER_REGISTER)
    first, second = records()
    assert first["event"] == "authz.decision"
    assert first["level"] == "warning"
    assert first["actor"] == {"iss": ISSUER, "sub": "sub-alice", "username": "alice", "via": "session"}
    assert (first["criterion"], first["action"], first["decision"]) == ("c1", "criterion.update", "deny")
    assert first["reason"] == DenyAll.REASON
    assert (second["criterion"], second["action"]) == (None, "user.register")


async def test_an_allowed_write_is_logged_and_authorized(records) -> None:
    permissions = Allow()
    granted = await Gate(permissions).authorize(SessionResolver(FakeDirectory()), Action.CRITERION_UPDATE, "c1")  # type: ignore[arg-type]
    assert (granted.actor.username, granted.action, granted.criterion_id) == ("alice", Action.CRITERION_UPDATE, "c1")
    assert permissions.asked == [("alice", Action.CRITERION_UPDATE, "c1")]
    (record,) = records()
    assert (record["decision"], record["level"], record["reason"]) == ("allow", "info", "allowed for the test")


async def test_an_unresolved_actor_is_refused_before_the_permissions_are_asked(records) -> None:
    permissions = Allow()
    directory = FakeDirectory(me=IamError("whoami", "unreachable"))
    with pytest.raises(DeniedError, match="no verified actor"):
        await Gate(permissions).authorize(SessionResolver(directory), Action.WATCH_START, "c1")  # type: ignore[arg-type]
    assert permissions.asked == []
    (record,) = records()
    assert (record["actor"], record["decision"]) == (None, "deny")


async def test_a_resolver_that_crashes_is_a_refusal() -> None:
    class Crashing:
        async def resolve(self) -> Actor:
            raise RuntimeError("boom")

    with pytest.raises(DeniedError, match=r"resolving it failed \(RuntimeError\)"):
        await Gate(Allow()).authorize(Crashing(), Action.WATCH_START, "c1")


async def test_failing_or_vague_permissions_are_a_refusal_and_log_no_secret(records) -> None:
    with pytest.raises(DeniedError, match=r"the permissions failed \(RuntimeError\)"):
        await Gate(Broken()).authorize(actor(), Action.CRITERION_CREATE, "c1")
    with pytest.raises(DeniedError, match="the permissions gave no decision"):
        await Gate(Vague()).authorize(actor(), Action.CRITERION_CREATE, "c1")
    logged = json.dumps(records())
    assert JWT not in logged


async def test_only_the_gate_makes_an_authorized_action() -> None:
    with pytest.raises(TypeError, match=r"made only by Gate\.authorize"):
        AuthorizedAction(actor(), Action.CRITERION_UPDATE, "c1", "forged")
    with pytest.raises(TypeError):
        AuthorizedAction(actor(), Action.CRITERION_UPDATE, "c1", "forged", key=object())


async def test_a_service_checks_it_holds_the_right_authorization(records) -> None:
    granted = await Gate(Allow()).authorize(actor(), Action.CRITERION_UPDATE, "c1")
    granted.require(Action.CRITERION_UPDATE, "c1")
    with pytest.raises(AuthorizationMismatchError, match=r"authorized for criterion\.update on criterion c1, not"):
        granted.require(Action.CRITERION_UPDATE, "c2")
    with pytest.raises(AuthorizationMismatchError):
        granted.require(Action.USER_DISABLE)


# --- the HTTP transport's tokens ---------------------------------------------------------------------------------


class FakeOIDC:
    def __init__(self, claims: dict[str, Any] | Exception) -> None:
        self.claims = claims
        self.calls = 0

    def introspect(self, token: str) -> dict[str, Any]:
        self.calls += 1
        if isinstance(self.claims, Exception):
            raise self.claims
        return self.claims

    def accepts(self, claims: dict[str, Any]) -> bool:
        return claims.get("active") is True and claims.get("iss") == ISSUER


GOOD = {"active": True, "iss": ISSUER, "sub": "sub-bob", "exp": 2_000_000_000, "azp": "catalyst-cli", "scope": "openid"}


async def test_an_accepted_token_is_cached_by_its_hash_not_the_token() -> None:
    now = [1_000_000_000.0]
    oidc = FakeOIDC(GOOD)
    verifier = CatalystUsersTokenVerifier(oidc, cache_ttl=300, clock=lambda: now[0])
    access = await verifier.verify_token(JWT)
    assert access is not None
    assert (access.subject, access.client_id, access.scopes, access.expires_at) == (
        "sub-bob",
        "catalyst-cli",
        ["openid"],
        2_000_000_000,
    )
    assert await verifier.verify_token(JWT) is not None
    assert oidc.calls == 1
    assert JWT not in repr(verifier._cache)
    now[0] += 31  # the cache never lasts more than 30 seconds, whatever it is given
    await verifier.verify_token(JWT)
    assert oidc.calls == 2


async def test_a_refused_token_or_an_unreachable_provider_is_no_access(records) -> None:
    assert await CatalystUsersTokenVerifier(FakeOIDC({**GOOD, "active": False})).verify_token(JWT) is None
    assert await CatalystUsersTokenVerifier(FakeOIDC({**GOOD, "iss": "https://elsewhere"})).verify_token(JWT) is None
    no_exp = {k: v for k, v in GOOD.items() if k != "exp"}
    assert await CatalystUsersTokenVerifier(FakeOIDC(no_exp)).verify_token(JWT) is None
    unreachable = FakeOIDC(ProviderUnavailableError("introspection failed with HTTP 503"))
    assert await CatalystUsersTokenVerifier(unreachable).verify_token(JWT) is None
    events = [r["event"] for r in records()]
    assert events == ["token.refused", "token.refused", "token.refused", "token.unverified"]


def test_the_http_transport_needs_the_api_client_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    from catalyst_mcp.settings import Settings

    monkeypatch.delenv("CATALYST_USERS_API_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("CATALYST_USERS_HOME", "/nonexistent-catalyst-users-home")
    with pytest.raises(ValueError, match="API client secret"):
        CatalystUsersTokenVerifier.from_settings(Settings())
    verifier = CatalystUsersTokenVerifier.from_settings(
        Settings(keycloak_url="https://id.example.org", keycloak_api_client_secret=SecretStr("s3cret"))
    )
    assert verifier.cache_ttl == 30.0
