"""The HTTP transport's bearer tokens (mcp-AUTHZ-000003): MCP's `TokenVerifier` over catalyst-users' introspection.

A token is accepted only when the provider says it is active, issued by this realm, meant for this service's API
client and not expired (catalyst-users' `OIDCClient.accepts`). An answer is cached at most 30 seconds, keyed by the
token's hash: the cache never holds the token. A token refused, or a provider that cannot be reached, is no access
token: the transport answers 401.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

import anyio.to_thread
from catalyst_users import config
from catalyst_users.errors import CatalystUsersError
from catalyst_users.oidc import OIDCClient
from mcp.server.auth.provider import AccessToken

from catalyst_mcp import log
from catalyst_mcp.settings import Settings

MAX_TTL = 30.0


class Introspector(Protocol):
    def introspect(self, token: str) -> dict[str, Any]: ...
    def accepts(self, claims: dict[str, Any]) -> bool: ...


def _access(token: str, claims: dict[str, Any]) -> AccessToken:
    scope = claims.get("scope")
    return AccessToken(
        token=token,
        client_id=str(claims.get("azp") or claims.get("client_id") or ""),
        scopes=scope.split() if isinstance(scope, str) else [],
        expires_at=int(claims["exp"]),
        subject=str(claims["sub"]),
        claims=claims,
    )


class CatalystUsersTokenVerifier:
    def __init__(
        self,
        oidc: Introspector,
        cache_ttl: float = MAX_TTL,
        cache_size: int = 10_000,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.oidc = oidc
        self.cache_ttl = min(cache_ttl, MAX_TTL)
        self.cache_size = cache_size
        self.clock = clock
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self.logger = log.get("token")

    @classmethod
    def from_settings(cls, settings: Settings) -> CatalystUsersTokenVerifier:
        users = config.load(settings.users_overrides())
        if not users.api_client_secret:
            raise ValueError(
                "the HTTP transport needs the API client secret to verify tokens (CATALYST_MCP_KEYCLOAK_API_CLIENT_SECRET)"
            )
        return cls(OIDCClient(users))

    async def verify_token(self, token: str) -> AccessToken | None:
        key = hashlib.sha256(token.encode()).hexdigest()
        now = self.clock()
        cached = self._cache.get(key)
        if cached and cached[0] > now:
            return _access(token, cached[1])
        try:
            claims = await anyio.to_thread.run_sync(self.oidc.introspect, token)
        except CatalystUsersError as err:
            log.event(self.logger, "token.unverified", level=logging.WARNING, reason=f"introspection failed ({err})")
            return None
        if not self.oidc.accepts(claims) or not claims.get("sub") or not isinstance(claims.get("exp"), int | float):
            self._cache.pop(key, None)
            log.event(self.logger, "token.refused", level=logging.WARNING, sub=claims.get("sub"))
            return None
        self._remember(key, min(now + self.cache_ttl, float(claims["exp"])), claims)
        return _access(token, claims)

    def _remember(self, key: str, until: float, claims: dict[str, Any]) -> None:
        if len(self._cache) >= self.cache_size:
            now = self.clock()
            for stale in [k for k, (exp, _) in self._cache.items() if exp <= now]:
                del self._cache[stale]
            while len(self._cache) >= self.cache_size:
                del self._cache[next(iter(self._cache))]  # oldest first
        self._cache[key] = (until, claims)
