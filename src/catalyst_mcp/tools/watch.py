"""Watchers: keep a criterion in step with its repository, on each saved file or each new commit."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from catalyst_mcp.settings import Settings
from catalyst_mcp.tools import READ_ONLY, WRITE, pending

WatchMode = Literal["file_change", "commit"]

RepoPath = Annotated[str, Field(description="Absolute path of the local git repository to watch")]
CriterionId = Annotated[str, Field(description="The criterion's ID (its unique name in the identity provider)")]


def register(app: MCPServer, _settings: Settings) -> None:
    @app.tool(annotations=WRITE)
    async def start_watch(repo_path: RepoPath, criterion_id: CriterionId, mode: WatchMode = "commit") -> dict[str, Any]:
        """Watch a repository and update its criterion on every saved file (`file_change`) or every new commit
        (`commit`). Each update is authorized against the identity that started the watch; a denied update is
        logged and changes nothing."""
        raise pending("start_watch", "5")

    @app.tool(annotations=WRITE)
    async def stop_watch(criterion_id: CriterionId) -> dict[str, Any]:
        """Stop watching the repository of a criterion."""
        raise pending("stop_watch", "5")

    @app.tool(annotations=READ_ONLY)
    async def list_watches() -> list[dict[str, Any]]:
        """The running watches: criterion, repository, mode, acting identity and last update."""
        raise pending("list_watches", "5")
