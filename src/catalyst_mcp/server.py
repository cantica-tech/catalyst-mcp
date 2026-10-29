"""The MCP server: its tools, and `catalyst-mcp`, which runs it over stdio or, optionally, streamable HTTP.

Over HTTP every request carries a bearer token the provider must accept (`CatalystUsersTokenVerifier`), and the
server only reads (mcp-AUTHZ-000004). The watches stop when the server does.
"""

from __future__ import annotations

import argparse
import contextlib
from collections.abc import AsyncIterator, Sequence
from typing import Any

from catalyst_users import config
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer

from catalyst_mcp import __version__, log
from catalyst_mcp.auth.token import CatalystUsersTokenVerifier
from catalyst_mcp.services import Services
from catalyst_mcp.settings import Settings
from catalyst_mcp.tools import criteria, templates, users, watch

INSTRUCTIONS = (
    "Keeps catalyst criteria in step with their git repositories. Every write is authorized against the caller's "
    "identity in the identity provider and refused by default; a refusal names the action and the criterion. Over "
    "HTTP the server only reads."
)


def build(settings: Settings, services: Services | None = None) -> MCPServer:
    services = services or Services.from_settings(settings)

    @contextlib.asynccontextmanager
    async def lifespan(_app: MCPServer[Any]) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await services.watches.aclose()

    http: dict[str, Any] = {}
    if settings.transport != "stdio":
        issuer = config.load(settings.users_overrides()).issuer
        resource = f"http://{settings.http_host}:{settings.http_port}{settings.http_path}"
        http = {
            "token_verifier": CatalystUsersTokenVerifier.from_settings(settings),
            # the verifier checks each token's audience itself (catalyst-users' `accepts`: this service's API client)
            "auth": AuthSettings(
                issuer_url=issuer,  # type: ignore[arg-type]
                resource_server_url=resource,  # type: ignore[arg-type]
                validate_token_resource=False,
            ),
        }
    app = MCPServer("catalyst-mcp", version=__version__, instructions=INSTRUCTIONS, lifespan=lifespan, **http)
    for register in (watch.register, criteria.register, templates.register, users.register):
        register(app, services)
    return app


def parse(argv: Sequence[str] | None = None) -> Settings:
    """The settings, with the command line's options over the environment's."""
    parser = argparse.ArgumentParser(prog="catalyst-mcp", description=__doc__)
    parser.add_argument("--version", action="version", version=f"catalyst-mcp {__version__}")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"))
    parser.add_argument("--host", dest="http_host")
    parser.add_argument("--port", dest="http_port", type=int)
    args = parser.parse_args(argv)
    return Settings(**{key: value for key, value in vars(args).items() if value is not None})


def main(argv: Sequence[str] | None = None) -> int:
    settings = parse(argv)
    log.configure(settings.log_level, settings.log_format)
    log.event(log.get("server"), "server.start", version=__version__, transport=settings.transport)
    app = build(settings)
    if settings.transport == "stdio":
        app.run()
    else:
        app.run(
            "streamable-http",
            host=settings.http_host,
            port=settings.http_port,
            streamable_http_path=settings.http_path,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
