"""People in the identity provider, through catalyst-users, which acts with the caller's own token."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from catalyst_mcp.settings import Settings
from catalyst_mcp.tools import READ_ONLY, WRITE, pending

Username = Annotated[str, Field(description="The person's username")]
Optional = Annotated[str | None, Field(description="Left unchanged when omitted")]


def register(app: MCPServer, _settings: Settings) -> None:
    @app.tool(annotations=WRITE)
    async def register_user(
        username: Username,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> dict[str, Any]:
        """Register a person. Registering one who exists with the same details changes nothing."""
        raise pending("register_user", "7")

    @app.tool(annotations=WRITE)
    async def update_user(
        username: Username,
        email: Optional = None,
        first_name: Optional = None,
        last_name: Optional = None,
        enabled: Annotated[bool | None, Field(description="Enable or disable; unchanged when omitted")] = None,
    ) -> dict[str, Any]:
        """Change a person's details."""
        raise pending("update_user", "7")

    @app.tool(annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True, open_world_hint=False))
    async def delete_user(username: Username) -> dict[str, Any]:
        """Remove a person's access. They are disabled, never deleted: what they signed keeps naming them."""
        raise pending("delete_user", "7")

    @app.tool(annotations=WRITE)
    async def assign_role(
        username: Username,
        role: Annotated[str, Field(description="The role to grant")],
        criterion_id: Annotated[str | None, Field(description="Grant it within this criterion only")] = None,
    ) -> dict[str, Any]:
        """Grant a person a role, across the realm or within one criterion."""
        raise pending("assign_role", "7")

    @app.tool(annotations=READ_ONLY)
    async def list_users(
        criterion_id: Annotated[str | None, Field(description="Only this criterion's members")] = None,
        search: Annotated[str | None, Field(description="Match on username, name or email")] = None,
    ) -> list[dict[str, Any]]:
        """The people in the realm, or in one criterion."""
        raise pending("list_users", "7")

    @app.tool(annotations=READ_ONLY)
    async def get_user(username: Username) -> dict[str, Any]:
        """One person: details, groups, roles and the criteria they belong to."""
        raise pending("get_user", "7")
