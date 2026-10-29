"""Criteria: create one, from rules or from a repository, with its group in the identity provider."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from catalyst_mcp.auth import Action
from catalyst_mcp.criterion import CriterionCreated
from catalyst_mcp.services import Services
from catalyst_mcp.tools import WRITE, tool_errors

Name = Annotated[str, Field(description="The criterion's name; it becomes its unique ID in the identity provider")]
Users = Annotated[list[str] | None, Field(description="Usernames to make members (a criterion served only)")]


def created(made: CriterionCreated) -> dict[str, Any]:
    return {
        "criterion_id": made.criterion_id,
        "repo_path": str(made.repo_path),
        "criterion_path": str(made.criterion_path),
        "owner": made.owner,
        "made": list(made.made),
        "members_added": list(made.members_added),
        "unchanged": made.unchanged,
    }


def register(app: MCPServer, services: Services) -> None:
    @app.tool(annotations=WRITE)
    async def create_criterion(
        name: Name,
        rules: Annotated[dict[str, Any], Field(description="The criterion's rules, validated before anything is made")],
        owner: Annotated[str, Field(description="Username of the criterion's owner (its first admin)")],
        users: Users = None,
    ) -> dict[str, Any]:
        """Create a criterion in a new repository under the projects root, and its group in the identity provider.
        Validated first; a failure before the group undoes what this call made; repeating a create completes it or
        changes nothing. One that exists with another owner or other rules is refused."""
        with tool_errors("create_criterion"):
            authorized = await services.authorize(Action.CRITERION_CREATE, name)
            return created(await services.criteria.create(authorized, name, rules, owner, users or ()))

    @app.tool(annotations=WRITE)
    async def create_criterion_from_repo(
        repo_path: Annotated[str, Field(description="Absolute path of the local git repository the criterion mirrors")],
        name: Name,
        users: Users = None,
    ) -> dict[str, Any]:
        """Create a criterion that governs an existing local git repository; the caller becomes its owner."""
        with tool_errors("create_criterion_from_repo"):
            authorized = await services.authorize(Action.CRITERION_CREATE, name)
            return created(await services.criteria.create_from_repo(authorized, repo_path, name, users or ()))
