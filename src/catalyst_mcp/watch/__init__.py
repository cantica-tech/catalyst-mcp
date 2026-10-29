"""Watching a criterion's repository (mcp-WATCH-000001..000004): git, the triggers, and the watches."""

from catalyst_mcp.watch.diff import Change, Git, GitError
from catalyst_mcp.watch.manager import Outcome, Watch, WatchManager, WatchRefusedError
from catalyst_mcp.watch.triggers import Mode, triggers

__all__ = [
    "Change",
    "Git",
    "GitError",
    "Mode",
    "Outcome",
    "Watch",
    "WatchManager",
    "WatchRefusedError",
    "triggers",
]
