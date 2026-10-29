"""The MCP tools (mcp-TOOLS-000003): thin functions that ask the gate, then call a service. Each module's `register`
adds its tools to the server over the shared services."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from catalyst_mcp import log
from catalyst_mcp.auth import AuthorizationMismatchError, DeniedError
from catalyst_mcp.criterion import CatalystCliError, CriterionExistsError, CriterionPartialError, InvalidCriterionError
from catalyst_mcp.iam import IamError
from catalyst_mcp.templates import InvalidTemplateError, TemplateNotFoundError
from catalyst_mcp.watch import GitError, WatchRefusedError

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

# what a tool's caller is told as it is: refusals, invalid arguments, conflicts, and failures of what it orchestrates
EXPECTED = (
    DeniedError,
    AuthorizationMismatchError,
    InvalidCriterionError,
    CriterionExistsError,
    CriterionPartialError,
    CatalystCliError,
    IamError,
    InvalidTemplateError,
    TemplateNotFoundError,
    WatchRefusedError,
    GitError,
)


@contextlib.contextmanager
def tool_errors(tool: str) -> Iterator[None]:
    """A failure as a tool error naming its cause, never a trace or a secret."""
    try:
        yield
    except EXPECTED as err:
        raise ToolError(log.redact(f"{tool}: {err}")) from err
    except ToolError:
        raise
    except Exception as err:
        log.event(log.get("tools"), "tool.failed", level=logging.ERROR, tool=tool, error=type(err).__name__)
        raise ToolError(f"{tool} failed unexpectedly ({type(err).__name__}); see the server's log") from err
