"""Who acts (mcp-AUTHZ-000003): a verified identity, resolved before any write, never guessed.

Over stdio the actor is the identity catalyst-users says is logged in, else the machine's system account (catalyst's
rr-META-000011); over HTTP, the caller whose access token the transport's verifier accepted (`auth/token.py`). A
watch acts as the identity that started it: `PinnedResolver` refuses any other. A commit's git author is never an
actor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from catalyst_users.provider import whoami_from_claims
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from catalyst_mcp.iam import IamError, IamNotLoggedInError, Identity, IdentityDirectory

Via = Literal["session", "system", "token"]


@dataclass(frozen=True)
class Actor:
    issuer: str
    subject: str
    username: str
    via: Via  # how it was resolved
    roles: tuple[str, ...] = ()
    grants: tuple[str, ...] = ()
    groups: tuple[str, ...] = ()
    administers: tuple[str, ...] = ()

    @classmethod
    def from_identity(cls, identity: Identity, via: Via) -> Actor:
        return cls(
            issuer=identity.issuer,
            subject=identity.subject,
            username=identity.username,
            via=via,
            roles=identity.roles,
            grants=identity.grants,
            groups=identity.groups,
            administers=identity.administers,
        )

    def same_identity(self, other: Actor) -> bool:
        """The same person: issuer and subject, never the username (which the provider may change)."""
        return (self.issuer, self.subject) == (other.issuer, other.subject)

    def describe(self) -> dict[str, str]:
        """What a log record says of the actor."""
        return {"iss": self.issuer, "sub": self.subject, "username": self.username, "via": self.via}


class ActorUnresolvedError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ActorResolver(Protocol):
    async def resolve(self) -> Actor: ...


class SessionResolver:
    """The identity logged in on this machine, else its system account; neither, unresolved."""

    def __init__(self, directory: IdentityDirectory) -> None:
        self.directory = directory

    async def resolve(self) -> Actor:
        try:
            return Actor.from_identity(await self.directory.whoami(), "session")
        except IamNotLoggedInError:
            pass
        except IamError as err:
            raise ActorUnresolvedError(f"the logged-in identity could not be verified ({err})") from err
        try:
            return Actor.from_identity(await self.directory.system_whoami(), "system")
        except IamError as err:
            raise ActorUnresolvedError(
                f"no one is logged in and the system account could not be verified ({err})"
            ) from err


class TokenResolver:
    """The caller of the current HTTP request: the claims of the access token the transport's verifier accepted."""

    def __init__(
        self,
        issuer: str,
        catalyst_client: str,
        current: Callable[[], AccessToken | None] = get_access_token,
    ) -> None:
        self.issuer = issuer
        self.catalyst_client = catalyst_client
        self.current = current

    async def resolve(self) -> Actor:
        token = self.current()
        if token is None or not token.claims or not token.claims.get("sub"):
            raise ActorUnresolvedError("the request carries no verified access token")
        return Actor.from_identity(whoami_from_claims(token.claims, self.issuer, self.catalyst_client), "token")


class PinnedResolver:
    """A watch's actor: whoever `inner` resolves now, only if it is the identity that started the watch."""

    def __init__(self, inner: ActorResolver, pinned: Actor) -> None:
        self.inner = inner
        self.pinned = pinned

    async def resolve(self) -> Actor:
        actor = await self.inner.resolve()
        if not actor.same_identity(self.pinned):
            raise ActorUnresolvedError(
                f"the identity that started this watch ({self.pinned.username}) is no longer the one verified "
                f"({actor.username})"
            )
        return actor
