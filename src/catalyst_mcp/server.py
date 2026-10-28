"""The MCP server: its tools, and `catalyst-mcp`, which runs it over stdio or, optionally, streamable HTTP."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from mcp.server.mcpserver import MCPServer

from catalyst_mcp import __version__, log
from catalyst_mcp.settings import Settings
from catalyst_mcp.tools import criteria, templates, users, watch

INSTRUCTIONS = (
    "Keeps catalyst criteria in step with their git repositories. Every write is authorized against the caller's "
    "identity in the identity provider and refused by default; a refusal names the action and the criterion."
)


def build(settings: Settings) -> MCPServer:
    app = MCPServer("catalyst-mcp", version=__version__, instructions=INSTRUCTIONS)
    for register in (watch.register, criteria.register, templates.register, users.register):
        register(app, settings)
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
