"""What an actor may do (mcp-AUTHZ-000001): `Permissions.can(actor, action, criterion_id) -> Decision`.

`RoleMapping` (mcp-AUTHZ-000005) is the mapping: the realm role `catalyst-admin` creates criteria and manages people
and roles; a criterion's admins, asked of the identity provider at the moment of the decision, update it and watch
it. Anything else is refused. `DenyAll` refuses everything.

`StdioOnly` (mcp-AUTHZ-000004) wraps whichever permissions there are: over HTTP it refuses every action, since
catalyst would sign and catalyst-users would act as the server's own identity, not the caller's.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from catalyst_mcp.auth.actor import Actor

if TYPE_CHECKING:
    from catalyst_mcp.iam import IdentityDirectory


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


ADMIN_ROLE = "catalyst-admin"
REALM_ACTIONS = frozenset(
    {Action.CRITERION_CREATE, Action.USER_REGISTER, Action.USER_UPDATE, Action.USER_DISABLE, Action.ROLE_ASSIGN}
)
CRITERION_ACTIONS = frozenset({Action.CRITERION_UPDATE, Action.WATCH_START, Action.WATCH_STOP})


class RoleMapping:
    def __init__(self, directory: IdentityDirectory) -> None:
        self.directory = directory

    async def can(self, actor: Actor, action: Action, criterion_id: str | None) -> Decision:
        if action in REALM_ACTIONS:
            if ADMIN_ROLE in actor.roles:
                return Decision.allow(f"{actor.username} has the realm role {ADMIN_ROLE}")
            return Decision.deny(f"{action} needs the realm role {ADMIN_ROLE}, which {actor.username} does not have")
        if action in CRITERION_ACTIONS:
            return await self._admin(actor, action, criterion_id)
        return Decision.deny(f"no one may {action}")

    async def _admin(self, actor: Actor, action: Action, criterion_id: str | None) -> Decision:
        if not criterion_id:
            return Decision.deny(f"{action} needs a criterion")
        try:
            criteria = await self.directory.criteria()
        except Exception as err:
            return Decision.deny(f"the admins of criterion {criterion_id} could not be asked ({type(err).__name__})")
        found = next((c for c in criteria if c.get("name") == criterion_id), None)
        if found is None:
            return Decision.deny(f"criterion {criterion_id} has no group in the identity provider")
        admins = found.get("admins")
        if admins is None:
            return Decision.deny(f"the admins of criterion {criterion_id} are not visible to this session")
        if actor.username in admins:
            return Decision.allow(f"{actor.username} is an admin of criterion {criterion_id}")
        return Decision.deny(f"{action} needs an admin of criterion {criterion_id}, which {actor.username} is not")


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
