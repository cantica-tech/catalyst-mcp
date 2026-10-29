"""Templates: versioned criterion templates, and criteria made from them."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from catalyst_mcp.auth import Action
from catalyst_mcp.services import Services
from catalyst_mcp.templates import create_from_template as create
from catalyst_mcp.tools import READ_ONLY, WRITE, tool_errors
from catalyst_mcp.tools.criteria import created

TemplateName = Annotated[str, Field(description="The template's name")]
Version = Annotated[str | None, Field(description="The template's version; the latest when omitted")]


def register(app: MCPServer, services: Services) -> None:
    @app.tool(annotations=READ_ONLY)
    async def list_templates() -> list[dict[str, Any]]:
        """The available templates: name, versions, latest and description, and any invalid version with its
        errors."""
        return services.templates.list()

    @app.tool(annotations=READ_ONLY)
    async def get_template(name: TemplateName, version: Version = None) -> dict[str, Any]:
        """One template, with the parameters it takes (a JSON Schema) and what it fills in."""
        with tool_errors("get_template"):
            return services.templates.get(name, version).describe()

    @app.tool(annotations=WRITE)
    async def create_from_template(
        name: TemplateName,
        params: Annotated[dict[str, Any], Field(description="Values for the template's parameters")],
        version: Version = None,
    ) -> dict[str, Any]:
        """Create a criterion from a template, exactly as create_criterion does."""
        with tool_errors("create_from_template"):
            filled = services.templates.render(name, params, version)
            authorized = await services.authorize(Action.CRITERION_CREATE, filled.name)
            return {**created(await create(services.criteria, authorized, filled)), "template": filled.template}
