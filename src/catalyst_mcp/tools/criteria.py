"""Criteria: create one, from rules or from a repository, with its container in the identity provider."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from catalyst_mcp.settings import Settings
from catalyst_mcp.tools import WRITE, pending

Name = Annotated[str, Field(description="The criterion's name; it becomes its unique ID in the identity provider")]
Users = Annotated[list[str] | None, Field(description="Usernames to make members of the criterion")]


def register(app: MCPServer, _settings: Settings) -> None:
    @app.tool(annotations=WRITE)
    async def create_criterion(
        name: Name,
        rules: Annotated[dict[str, Any], Field(description="The criterion's rules, validated before anything is made")],
        owner: Annotated[str, Field(description="Username of the criterion's owner (its first admin)")],
        users: Users = None,
    ) -> dict[str, Any]:
        """Create a criterion and its container in the identity provider, all or nothing. Creating one that already
        exists with the same owner changes nothing; with another owner it is refused."""
        raise pending("create_criterion", "4")

    @app.tool(annotations=WRITE)
    async def create_criterion_from_repo(
        repo_path: Annotated[str, Field(description="Absolute path of the local git repository the criterion mirrors")],
        name: Name,
        users: Users = None,
    ) -> dict[str, Any]:
        """Create a criterion that mirrors a local git repository; the caller becomes its owner."""
        raise pending("create_criterion_from_repo", "4")
