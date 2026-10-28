"""What an actor may do (mcp-AUTHZ-000001): `Permissions.can(actor, action, criterion_id) -> Decision`.

The roles mapping is a pending input. Until it is defined, `DenyAll` refuses everything, so no write can succeed:
intended. A real mapping replaces `DenyAll` here and nowhere else.
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
