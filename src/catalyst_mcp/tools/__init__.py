"""The MCP tools: thin functions that validate their arguments and call a service. Each module's `register` adds
its tools to the server."""

from __future__ import annotations

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


def pending(tool: str, step: str) -> ToolError:
    """The error a tool raises until its implementation step (docs/design.md, Implementation) lands."""
    return ToolError(f"{tool} is not implemented yet (implementation step {step})")
