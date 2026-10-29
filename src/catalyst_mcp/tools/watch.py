"""Watchers: keep a criterion in step with its repository, on each new commit or each saved file."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from catalyst_mcp.auth import Action
from catalyst_mcp.services import Services
from catalyst_mcp.tools import READ_ONLY, WRITE, tool_errors
from catalyst_mcp.watch import Mode

RepoPath = Annotated[str, Field(description="Absolute path of the local git repository to watch")]
CriterionId = Annotated[str, Field(description="The criterion's ID (its unique name in the identity provider)")]


def register(app: MCPServer, services: Services) -> None:
    @app.tool(annotations=WRITE)
    async def start_watch(repo_path: RepoPath, criterion_id: CriterionId, mode: Mode = "commit") -> dict[str, Any]:
        """Watch a repository and keep its criterion in step: on every new commit (`commit`), the commits made by
        hand and not yet pushed are adopted into the criterion's journal; on every saved file (`file_change`), the
        working tree's diff is refreshed and nothing is written. Each update is authorized again as the identity that
        started the watch; a refused update is logged and changes nothing. Over stdio only."""
        with tool_errors("start_watch"):
            authorized = await services.authorize(Action.WATCH_START, criterion_id)
            watch, started = await services.watches.start(authorized, repo_path, criterion_id, mode)
            return {**watch.describe(), "started": started}

    @app.tool(annotations=WRITE)
    async def stop_watch(criterion_id: CriterionId) -> dict[str, Any]:
        """Stop watching the repository of a criterion."""
        with tool_errors("stop_watch"):
            authorized = await services.authorize(Action.WATCH_STOP, criterion_id)
            return {"criterion_id": criterion_id, "stopped": await services.watches.stop(authorized, criterion_id)}

    @app.tool(annotations=READ_ONLY)
    async def list_watches() -> list[dict[str, Any]]:
        """The running watches: criterion, repository, mode, acting identity, last commit, diff and last outcome."""
        return services.watches.list()
