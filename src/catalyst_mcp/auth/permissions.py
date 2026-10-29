"""What an actor may do (mcp-AUTHZ-000001): `Permissions.can(actor, action, criterion_id) -> Decision`.

The roles mapping is a pending input. Until it is defined, `DenyAll` refuses everything, so no write can succeed:
intended. A real mapping replaces `DenyAll` here and nowhere else.

`StdioOnly` (mcp-AUTHZ-000004) wraps whichever permissions there are: over HTTP it refuses every action, since
catalyst would sign and catalyst-users would act as the server's own identity, not the caller's.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from catalyst_mcp.auth.actor import Actor


class Action(StrEnum):
    CRITERION_CREATE = "criterion.create"
    CRITERION_UPDATE = "criterion.update"
    WATCH_START = "watch.start"
    WATCH_STOP = "watch.stop"
    USER_REGISTER = "user.register"
    USER_UPDATE = "user.update"
    USER_DISABLE = "user.disable"
    ROLE_ASSIGN = "role.assign"
    USER_READ = "user.read"  # asked only over HTTP, where it is refused: people are read with the server's session


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str

    @classmethod
    def allow(cls, reason: str) -> Decision:
        return cls(True, reason)

    @classmethod
    def deny(cls, reason: str) -> Decision:
        return cls(False, reason)


class Permissions(Protocol):
    async def can(self, actor: Actor, action: Action, criterion_id: str | None) -> Decision: ...


class DenyAll:
    REASON = "no permissions are configured: every write is refused until the roles mapping is defined"

    async def can(self, actor: Actor, action: Action, criterion_id: str | None) -> Decision:
        return Decision.deny(self.REASON)


class StdioOnly:
    REASON = (
        "over HTTP catalyst would sign and catalyst-users would act as this server, not the caller: writes and reads "
        "of people are served over stdio only"
    )

    def __init__(self, inner: Permissions, transport: str) -> None:
        self.inner = inner
        self.transport = transport

    async def can(self, actor: Actor, action: Action, criterion_id: str | None) -> Decision:
        if self.transport != "stdio":
            return Decision.deny(self.REASON)
        return await self.inner.can(actor, action, criterion_id)
