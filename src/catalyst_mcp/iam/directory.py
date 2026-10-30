"""The identity directory (mcp-IAM-000001): what catalyst-mcp asks of the identity provider, whichever provider it is.

A person is disabled, never deleted (mcp-IAM-000002), so the directory has no delete. A criterion's members are only
read here: catalyst changes them and records each change (catalyst-users' docs/membership.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from catalyst_users.provider import Whoami as Identity

__all__ = [
    "IamConflictError",
    "IamError",
    "IamNotFoundError",
    "IamNotLoggedInError",
    "Identity",
    "IdentityDirectory",
    "Person",
    "Registered",
    "Role",
]


@dataclass(frozen=True)
class Person:
    username: str
    email: str | None
    name: str
    enabled: bool


@dataclass(frozen=True)
class Registered:
    person: Person
    created: bool  # False: the person already existed with these details, and nothing changed


@dataclass(frozen=True)
class Role:
    name: str
    description: str


class IamError(Exception):
    """A directory operation that failed. The message names the operation and never carries a token."""

    def __init__(self, operation: str, message: str, status: int | None = None) -> None:
        self.operation = operation
        self.status = status
        super().__init__(f"{operation}: {message}")


class IamNotLoggedInError(IamError):
    """No one is logged in to the provider on this machine."""


class IamNotFoundError(IamError):
    pass


class IamConflictError(IamError):
    pass


class IdentityDirectory(Protocol):
    async def whoami(self) -> Identity: ...
    async def system_whoami(self) -> Identity: ...

    async def list_users(self, search: str | None = None, limit: int = 100) -> list[Person]: ...
    async def get_user(self, username: str) -> Person: ...
    async def user_groups(self, username: str) -> list[str]: ...
    async def register_user(
        self,
        username: str,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> Registered: ...
    async def update_user(
        self,
        username: str,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> Person: ...
    async def set_enabled(self, username: str, enabled: bool) -> Person: ...

    async def roles(self) -> list[Role]: ...
    async def grant_role(self, username: str, role: str) -> None: ...

    async def criteria(self) -> list[dict[str, Any]]: ...
    async def add_criterion(self, name: str, admin: str | None = None) -> list[str]: ...
    async def criterion_members(self, name: str) -> list[str]: ...
