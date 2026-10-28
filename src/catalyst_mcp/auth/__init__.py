"""Authorizing every write (mcp-AUTHZ-000001..000003): who acts, what they may do, and the gate between them."""

from catalyst_mcp.auth.actor import (
    Actor,
    ActorResolver,
    ActorUnresolvedError,
    PinnedResolver,
    SessionResolver,
    TokenResolver,
)
from catalyst_mcp.auth.gate import AuthorizationMismatchError, AuthorizedAction, DeniedError, Gate
from catalyst_mcp.auth.permissions import Action, Decision, DenyAll, Permissions

__all__ = [
    "Action",
    "Actor",
    "ActorResolver",
    "ActorUnresolvedError",
    "AuthorizationMismatchError",
    "AuthorizedAction",
    "Decision",
    "DeniedError",
    "DenyAll",
    "Gate",
    "Permissions",
    "PinnedResolver",
    "SessionResolver",
    "TokenResolver",
]
