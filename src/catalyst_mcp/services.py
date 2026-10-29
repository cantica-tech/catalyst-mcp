"""What the tools share (mcp-TOOLS-000003): the directory, the gate and the actor of the transport, and the services
that write only with the gate's authorization.

Over stdio the actor is the identity logged in on this machine; over HTTP, the caller of the request, and the
permissions refuse every write there (`StdioOnly`, mcp-AUTHZ-000004).
"""

from __future__ import annotations

from dataclasses import dataclass

from catalyst_users import config

from catalyst_mcp.auth import (
    Action,
    ActorResolver,
    AuthorizedAction,
    Gate,
    Permissions,
    RoleMapping,
    SessionResolver,
    StdioOnly,
    TokenResolver,
)
from catalyst_mcp.criterion import CatalystCli, CriterionService
from catalyst_mcp.iam import CatalystUsersDirectory, IdentityDirectory
from catalyst_mcp.settings import Settings
from catalyst_mcp.templates import TemplateCatalog
from catalyst_mcp.watch import WatchManager


@dataclass
class Services:
    settings: Settings
    directory: IdentityDirectory
    gate: Gate
    resolver: ActorResolver  # the actor of this transport
    cli: CatalystCli
    criteria: CriterionService
    watches: WatchManager
    templates: TemplateCatalog

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        directory: IdentityDirectory | None = None,
        permissions: Permissions | None = None,
        resolver: ActorResolver | None = None,
        cli: CatalystCli | None = None,
    ) -> Services:
        directory = directory or CatalystUsersDirectory.from_settings(settings)
        gate = Gate(StdioOnly(permissions or RoleMapping(directory), settings.transport))
        resolver = resolver or _resolver(settings, directory)
        cli = cli or CatalystCli.from_settings(settings)
        # a watch triggers outside any request: the identity verified then is this machine's session's
        watches = WatchManager(gate, SessionResolver(directory), cli, settings)
        return cls(
            settings=settings,
            directory=directory,
            gate=gate,
            resolver=resolver,
            cli=cli,
            criteria=CriterionService(cli, directory, settings),
            watches=watches,
            templates=TemplateCatalog(settings.templates_dir),
        )

    @property
    def over_http(self) -> bool:
        return self.settings.transport != "stdio"

    async def authorize(self, action: Action, criterion_id: str | None = None) -> AuthorizedAction:
        """The gate's authorization for `action`, as this transport's actor; `DeniedError` when refused."""
        return await self.gate.authorize(self.resolver, action, criterion_id)


def _resolver(settings: Settings, directory: IdentityDirectory) -> ActorResolver:
    if settings.transport == "stdio":
        return SessionResolver(directory)
    users = config.load(settings.users_overrides())
    return TokenResolver(users.issuer, users.catalyst_client)
