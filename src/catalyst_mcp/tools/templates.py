"""Templates: versioned criterion templates, and criteria made from them."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from catalyst_mcp.settings import Settings
from catalyst_mcp.tools import READ_ONLY, WRITE, pending

TemplateName = Annotated[str, Field(description="The template's name")]
Version = Annotated[str | None, Field(description="The template's version; the latest when omitted")]


def register(app: MCPServer, _settings: Settings) -> None:
    @app.tool(annotations=READ_ONLY)
    async def list_templates() -> list[dict[str, Any]]:
        """The available templates: name, versions and description."""
        raise pending("list_templates", "6")

    @app.tool(annotations=READ_ONLY)
    async def get_template(name: TemplateName, version: Version = None) -> dict[str, Any]:
        """One template, with the parameters it takes."""
        raise pending("get_template", "6")

    @app.tool(annotations=WRITE)
    async def create_from_template(
        name: TemplateName,
        params: Annotated[dict[str, Any], Field(description="Values for the template's parameters")],
        version: Version = None,
    ) -> dict[str, Any]:
        """Create a criterion from a template, as create_criterion does."""
        raise pending("create_from_template", "6")
