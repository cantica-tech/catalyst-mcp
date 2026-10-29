"""What wakes a watch (mcp-WATCH-000002, mcp-WATCH-000003), over `watchfiles`' asynchronous watcher.

`commit`: HEAD, the refs and packed-refs in the git directories, so nothing is installed in the project. `file_change`:
the working tree, `.git/` and git-ignored files excluded. Both are debounced; each yields the paths that changed,
relative to the repository.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import anyio
from watchfiles import Change as FsChange, awatch

from catalyst_mcp.watch.diff import Git

Mode = Literal["file_change", "commit"]


async def commit_triggers(git: Git, debounce_ms: int, stop: anyio.Event) -> AsyncIterator[frozenset[str]]:
    own, common = await git.git_dirs()
    head, refs, packed = own / "HEAD", common / "refs", common / "packed-refs"

    def wanted(_change: FsChange, path: str) -> bool:
        p = Path(path)
        return p in (head, packed) or p.is_relative_to(refs)

    dirs = {str(own), str(common)}
    async for changes in awatch(*dirs, watch_filter=wanted, debounce=debounce_ms, stop_event=stop):
        yield frozenset(_relative(Path(path), common.parent) for _, path in changes)


async def file_triggers(git: Git, debounce_ms: int, stop: anyio.Event) -> AsyncIterator[frozenset[str]]:
    root = await git.toplevel()

    def outside_git(_change: FsChange, path: str) -> bool:
        return ".git" not in Path(_relative(Path(path), root)).parts

    async for changes in awatch(root, watch_filter=outside_git, debounce=debounce_ms, stop_event=stop):
        paths = {_relative(Path(path), root) for _, path in changes}
        kept = paths - await git.ignored(paths)
        if kept:
            yield frozenset(kept)


def triggers(git: Git, mode: Mode, debounce_ms: int, stop: anyio.Event) -> AsyncIterator[frozenset[str]]:
    return (commit_triggers if mode == "commit" else file_triggers)(git, debounce_ms, stop)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
