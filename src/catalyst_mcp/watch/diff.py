"""git, as an asynchronous subprocess (mcp-WATCH-000004): the commits and files a watch reports.

Only reads: a watch writes to the criterion through catalyst, never through git.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from catalyst_mcp import log


class GitError(Exception):
    def __init__(self, args: Iterable[str], returncode: int, output: str) -> None:
        self.returncode = returncode
        self.output = log.redact(output.strip()[-600:])
        super().__init__(f"git {' '.join(args)} failed (exit {returncode}): {self.output}")


@dataclass(frozen=True)
class Change:
    path: str
    status: str  # git's letter: A added, M modified, D deleted, ? untracked, ...


class Git:
    def __init__(self, repo: Path) -> None:
        self.repo = repo

    async def run(self, *args: str, stdin: str | None = None) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=self.repo,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate(stdin.encode() if stdin is not None else None)
        if proc.returncode != 0:
            raise GitError(args, proc.returncode or 1, err.decode(errors="replace"))
        return out.decode(errors="replace")

    async def is_work_tree(self) -> bool:
        try:
            return (await self.run("rev-parse", "--is-inside-work-tree")).strip() == "true"
        except (GitError, OSError):
            return False

    async def toplevel(self) -> Path:
        return Path((await self.run("rev-parse", "--show-toplevel")).strip())

    async def git_dirs(self) -> tuple[Path, Path]:
        """This working tree's git directory (its HEAD) and the common one (refs, packed-refs)."""
        out = await self.run("rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir")
        own, common = out.splitlines()[:2]
        return Path(own), Path(common)

    async def head(self) -> str | None:
        """The commit HEAD names; None in a repository with no commit yet."""
        try:
            return (await self.run("rev-parse", "--verify", "--quiet", "HEAD^{commit}")).strip() or None
        except GitError:
            return None

    async def changed(self, base: str | None, head: str) -> list[Change]:
        """The files changed from `base` to `head`; from the empty tree when `base` is None."""
        if base:
            return _name_status(await self.run("diff", "--name-status", "--no-renames", "-z", base, head))
        out = await self.run("diff-tree", "--root", "--no-commit-id", "-r", "--name-status", "--no-renames", "-z", head)
        return _name_status(out)

    async def local_commits(self, base: str | None, head: str) -> set[str]:
        """The commits after `base` up to `head` that no remote-tracking branch holds: made here, not yet pushed."""
        return set((await self.run("rev-list", head, "--not", *([base] if base else []), "--remotes")).split())

    async def working_tree(self) -> list[Change]:
        """The working tree's changes against HEAD, untracked files included, ignored ones not."""
        out = await self.run("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
        changes = []
        for entry in out.split("\0"):
            if len(entry) > 3:
                code = entry[:2]
                changes.append(Change(entry[3:], "?" if code == "??" else (code[1].strip() or code[0])))
        return changes

    async def ignored(self, paths: Iterable[str]) -> set[str]:
        """Which of `paths` git ignores."""
        listed = "\0".join(paths)
        if not listed:
            return set()
        try:
            out = await self.run("check-ignore", "-z", "--stdin", stdin=listed + "\0")
        except GitError as err:
            if err.returncode == 1:  # none of them is ignored
                return set()
            raise
        return {path for path in out.split("\0") if path}


def _name_status(out: str) -> list[Change]:
    tokens = [token for token in out.split("\0") if token]
    return [Change(path, status[0]) for status, path in zip(tokens[::2], tokens[1::2], strict=False)]
