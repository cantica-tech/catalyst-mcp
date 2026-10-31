"""An MCP server that keeps catalyst criteria in step with their git repositories (docs/design.md).

It orchestrates: criteria are written through the catalyst command line, people and roles through catalyst-users.
What it owns is the watcher, the authorization gate every write goes through, and the tool surface.
"""

__version__ = "0.4.1"
