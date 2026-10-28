"""The gate (mcp-AUTHZ-000001, mcp-AUTHZ-000002): the only maker of an `AuthorizedAction`, which every write needs.

`Gate.authorize` resolves the actor first, asks the permissions, and logs the decision, allowed or refused. Anything
that goes wrong on the way is a refusal: an actor that cannot be resolved, permissions that fail or answer with
something other than a `Decision`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from catalyst_mcp import log
from catalyst_mcp.auth.actor import Actor, ActorResolver, ActorUnresolvedError
from catalyst_mcp.auth.permissions import Action, Decision, Permissions

_GATE = object()  # only the gate holds it: an AuthorizedAction made without it is refused


@dataclass(frozen=True)
class AuthorizedAction:
    actor: Actor
    action: Action
    criterion_id: str | None
    reason: str
    key: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.key is not _GATE:
            raise TypeError("an AuthorizedAction is made only by Gate.authorize (mcp-AUTHZ-000001)")

    def require(self, action: Action, criterion_id: str | None = None) -> None:
        """A service's check that it was given the authorization for what it is about to do."""
        if (self.action, self.criterion_id) != (action, criterion_id):
            raise AuthorizationMismatchError(self, action, criterion_id)


class AuthorizationMismatchError(Exception):
    def __init__(self, given: AuthorizedAction, action: Action, criterion_id: str | None) -> None:
        super().__init__(
            f"authorized for {given.action} on {_on(given.criterion_id)}, not {action} on {_on(criterion_id)}"
        )


class DeniedError(Exception):
    """A refusal; its message names the action and the criterion."""

    def __init__(self, action: Action, criterion_id: str | None, reason: str) -> None:
        self.action = action
        self.criterion_id = criterion_id
        self.reason = reason
        super().__init__(f"refused: {action} on {_on(criterion_id)}: {reason}")


def _on(criterion_id: str | None) -> str:
    return f"criterion {criterion_id}" if criterion_id else "the realm"


class Gate:
    def __init__(self, permissions: Permissions, logger: logging.Logger | None = None) -> None:
        self.permissions = permissions
        self.logger = logger or log.get("authz")

    async def authorize(
        self, who: Actor | ActorResolver, action: Action, criterion_id: str | None = None
    ) -> AuthorizedAction:
        resolved = who if isinstance(who, Actor) else await self._resolve(who)
        actor = resolved if isinstance(resolved, Actor) else None
        decision = await self._ask(resolved, action, criterion_id) if isinstance(resolved, Actor) else resolved
        self._log(actor, action, criterion_id, decision)
        if actor is None or not decision.allowed:
            raise DeniedError(action, criterion_id, decision.reason)
        return AuthorizedAction(actor, action, criterion_id, decision.reason, key=_GATE)

    async def _resolve(self, resolver: ActorResolver) -> Actor | Decision:
        """The actor, or the refusal that no actor could be verified."""
        try:
            return await resolver.resolve()
        except ActorUnresolvedError as err:
            return Decision.deny(f"no verified actor: {err.reason}")
        except Exception as err:
            return Decision.deny(f"no verified actor: resolving it failed ({type(err).__name__})")

    async def _ask(self, actor: Actor, action: Action, criterion_id: str | None) -> Decision:
        try:
            decision = await self.permissions.can(actor, action, criterion_id)
        except Exception as err:
            return Decision.deny(f"the permissions failed ({type(err).__name__})")
        if not isinstance(decision, Decision):
            return Decision.deny("the permissions gave no decision")
        return decision

    def _log(self, actor: Actor | None, action: Action, criterion_id: str | None, decision: Decision) -> None:
        log.event(
            self.logger,
            "authz.decision",
            level=logging.INFO if decision.allowed else logging.WARNING,
            actor=actor.describe() if actor else None,
            criterion=criterion_id,
            action=str(action),
            decision="allow" if decision.allowed else "deny",
            reason=decision.reason,
        )
