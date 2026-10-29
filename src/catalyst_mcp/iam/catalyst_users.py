"""The directory over catalyst-users' provider facade (mcp-IAM-000001).

The provider is chosen by catalyst-users' own settings and catalyst-mcp's overrides, and loaded on first use, so the
server starts without reaching the provider. It acts with the caller's own token: the provider decides what that
account may change. catalyst-users' core is synchronous, so every call runs in a worker thread (mcp-IAM-000003).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, TypeVar

import anyio.to_thread
from catalyst_users import config
from catalyst_users.errors import AdminError, CatalystUsersError, NotFoundError, NotLoggedInError
from catalyst_users.provider import Provider, from_pointer

from catalyst_mcp.iam.directory import (
    IamConflictError,
    IamError,
    IamNotFoundError,
    IamNotLoggedInError,
    Identity,
    Person,
    Registered,
    Role,
)
from catalyst_mcp.settings import Settings

T = TypeVar("T")
NOT_FOUND = 404
CONFLICT = 409


def _person(user: Any) -> Person:
    return Person(username=user.username, email=user.email, name=user.name, enabled=user.enabled)


def _differs(person: Person, email: str | None, first_name: str | None, last_name: str | None) -> list[str]:
    """The details given that are not the person's (a detail not given is not compared)."""
    name = " ".join(p for p in (first_name, last_name) if p)
    found = []
    if email is not None and (person.email or "").lower() != email.lower():
        found.append("email")
    if name and person.name != name:
        found.append("name")
    return found


class CatalystUsersDirectory:
    def __init__(self, load: Callable[[], Provider]) -> None:
        self._load = load
        self._provider: Provider | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, settings: Settings) -> CatalystUsersDirectory:
        pointer_file = str(settings.users_pointer) if settings.users_pointer else None

        def load() -> Provider:
            return from_pointer(config.pointer(settings.users_overrides(), pointer_file=pointer_file))

        return cls(load)

    def provider(self) -> Provider:
        """The provider, loaded once; called in a worker thread."""
        with self._lock:
            if self._provider is None:
                self._provider = self._load()
            return self._provider

    async def _run(self, operation: str, call: Callable[[], T]) -> T:
        try:
            return await anyio.to_thread.run_sync(call)
        except IamError:
            raise
        except AdminError as err:  # NotFoundError included: the admin API answers a missing role with a plain 404
            kind = {NOT_FOUND: IamNotFoundError, CONFLICT: IamConflictError}.get(err.status, IamError)
            raise kind(operation, str(err), err.status) from err
        except NotLoggedInError as err:
            raise IamNotLoggedInError(operation, str(err)) from err
        except CatalystUsersError as err:
            raise IamError(operation, str(err)) from err

    # --- the verified identity ---------------------------------------------------------------------------------

    async def whoami(self) -> Identity:
        return await self._run("whoami", lambda: self.provider().whoami())

    async def system_whoami(self) -> Identity:
        return await self._run("system_whoami", lambda: self.provider().system_whoami())

    # --- people ------------------------------------------------------------------------------------------------

    async def list_users(self, search: str | None = None, limit: int = 100) -> list[Person]:
        return await self._run(
            "list_users", lambda: [_person(u) for u in self.provider().directory().list_users(search, limit)]
        )

    async def get_user(self, username: str) -> Person:
        return await self._run("get_user", lambda: _person(self.provider().directory().user(username)))

    async def user_groups(self, username: str) -> list[str]:
        return await self._run("user_groups", lambda: list(self.provider().directory().user_groups(username)))

    async def register_user(
        self,
        username: str,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> Registered:
        """Register a person. One who exists with the details given is returned unchanged; with other details,
        `IamConflictError`: catalyst-users cannot change a person's details."""

        def existing(directory: Any) -> Registered:
            person = _person(directory.user(username))
            differs = _differs(person, email, first_name, last_name)
            if differs:
                raise IamConflictError(
                    "register_user", f"{username} exists with another {' and '.join(differs)}", CONFLICT
                )
            return Registered(person, created=False)

        def register() -> Registered:
            directory = self.provider().directory()
            try:
                return existing(directory)
            except NotFoundError:
                pass
            try:
                user = directory.create_user(username, email=email, first_name=first_name, last_name=last_name)
            except AdminError as err:
                if err.status != CONFLICT:
                    raise
                return existing(directory)  # registered meanwhile: compare with what is there now
            return Registered(_person(user), created=True)

        return await self._run("register_user", register)

    async def set_enabled(self, username: str, enabled: bool) -> Person:
        return await self._run(
            "set_enabled", lambda: _person(self.provider().directory().set_enabled(username, enabled))
        )

    # --- roles -------------------------------------------------------------------------------------------------

    async def roles(self) -> list[Role]:
        return await self._run(
            "roles", lambda: [Role(name, description) for name, description in self.provider().directory().roles()]
        )

    async def grant_role(self, username: str, role: str) -> None:
        await self._run("grant_role", lambda: self.provider().directory().grant_role_to_user(username, role))

    # --- criteria ----------------------------------------------------------------------------------------------

    async def criteria(self) -> list[dict[str, Any]]:
        return await self._run("criteria", lambda: list(self.provider().criteria()))

    async def add_criterion(self, name: str, admin: str | None = None) -> list[str]:
        """Add a criterion's groups, appointing `admin`; idempotent: returns what was added."""
        return await self._run("add_criterion", lambda: list(self.provider().add_criterion(name, admin) or []))

    async def criterion_members(self, name: str) -> list[str]:
        return await self._run("criterion_members", lambda: list(self.provider().members(name)))
