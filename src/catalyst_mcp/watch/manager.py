"""The watches (mcp-WATCH-000001..000004): started and stopped with the gate's authorization, each update authorized
again as the identity that started the watch.

A commit trigger asks catalyst which new commits carry changes not in the journal, keeps those made here and not yet
pushed, and only for those asks the gate for `criterion.update`, through a `PinnedResolver`: allowed, it adopts them;
refused or failed, nothing is written and the last processed commit stays, so the next trigger tries again. A
file-change trigger refreshes the working-tree diff and writes nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import tomllib
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio

from catalyst_mcp import log
from catalyst_mcp.auth import Action, Actor, ActorResolver, AuthorizedAction, DeniedError, Gate, PinnedResolver
from catalyst_mcp.criterion.catalyst_cli import CatalystCli, CatalystCliError
from catalyst_mcp.settings import Settings
from catalyst_mcp.watch.diff import Change, Git, GitError
from catalyst_mcp.watch.triggers import Mode, triggers

POINTER = "catalyst.toml"
SHOWN = 50  # paths and commits a record or a listing names at most

Triggers = Callable[[Git, Mode, int, anyio.Event], AsyncIterator[frozenset[str]]]


class WatchRefusedError(Exception):
    """A watch that cannot start: not a governed repository, or its criterion already watched otherwise."""


@dataclass
class Watch:
    criterion_id: str
    repo: Path
    mode: Mode
    actor: Actor  # the identity that started it: every update must be made as it
    started: str
    last_commit: str | None
    diff: list[Change] = field(default_factory=list)
    last_trigger: str | None = None
    last_outcome: str | None = None
    stop: anyio.Event = field(default_factory=anyio.Event, repr=False)
    task: asyncio.Task[None] | None = field(default=None, repr=False)

    def describe(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "repo_path": str(self.repo),
            "mode": self.mode,
            "actor": self.actor.describe(),
            "started": self.started,
            "last_commit": self.last_commit,
            "diff": [{"path": c.path, "status": c.status} for c in self.diff[:SHOWN]],
            "diff_count": len(self.diff),
            "last_trigger": self.last_trigger,
            "last_outcome": self.last_outcome,
            "running": self.task is not None and not self.task.done(),
        }


@dataclass(frozen=True)
class Outcome:
    outcome: str  # no new commit, nothing to adopt, adopted, refused, failed, diff refreshed
    reason: str = ""
    commits: tuple[dict[str, Any], ...] = ()
    adopted: tuple[str, ...] = ()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class WatchManager:
    def __init__(
        self,
        gate: Gate,
        resolver: ActorResolver,
        cli: CatalystCli,
        settings: Settings,
        source: Triggers = triggers,
    ) -> None:
        self.gate = gate
        self.resolver = resolver  # who is verified when a watch triggers; it must be the watch's actor
        self.cli = cli
        self.settings = settings
        self.source = source
        self.watches: dict[str, Watch] = {}
        self.lock = asyncio.Lock()
        self.logger = log.get("watch")

    async def start(
        self, authorized: AuthorizedAction, repo_path: str | Path, criterion_id: str, mode: Mode = "commit"
    ) -> tuple[Watch, bool]:
        """The watch of `criterion_id`, and whether this call started it (mcp-WATCH-000001)."""
        authorized.require(Action.WATCH_START, criterion_id)
        repo = Path(repo_path).expanduser()
        git = Git(repo)
        if not repo.is_absolute() or not await git.is_work_tree():
            raise WatchRefusedError(f"{repo} is not the absolute path of a git working tree")
        repo = await git.toplevel()
        git = Git(repo)
        name = _project_name(repo)
        if name != criterion_id:
            governed = f"governed by criterion {name}" if name else "not governed by catalyst (no catalyst.toml)"
            raise WatchRefusedError(f"{repo} is {governed}, not by {criterion_id}")
        async with self.lock:
            existing = self.watches.get(criterion_id)
            if existing:
                if (existing.repo, existing.mode) == (repo, mode) and existing.actor.same_identity(authorized.actor):
                    return existing, False
                raise WatchRefusedError(
                    f"criterion {criterion_id} is already watched ({existing.repo}, {existing.mode}, as "
                    f"{existing.actor.username}); stop that watch first"
                )
            watch = Watch(criterion_id, repo, mode, authorized.actor, _now(), await git.head())
            if mode == "file_change":
                watch.diff = await git.working_tree()
            watch.task = asyncio.create_task(self._run(watch, git), name=f"watch {criterion_id}")
            self.watches[criterion_id] = watch
        log.event(
            self.logger,
            "watch.started",
            criterion=criterion_id,
            repo=str(repo),
            mode=mode,
            actor=watch.actor.describe(),
            last_commit=watch.last_commit,
        )
        return watch, True

    async def stop(self, authorized: AuthorizedAction, criterion_id: str) -> bool:
        """Stop the watch of `criterion_id`; False when there was none."""
        authorized.require(Action.WATCH_STOP, criterion_id)
        async with self.lock:
            watch = self.watches.pop(criterion_id, None)
        if watch is None:
            return False
        await _halt(watch)
        log.event(self.logger, "watch.stopped", criterion=criterion_id, actor=authorized.actor.describe())
        return True

    def list(self) -> list[dict[str, Any]]:
        return [watch.describe() for watch in self.watches.values()]

    async def aclose(self) -> None:
        """Stop every watch, as the server shuts down: watches are not kept across a restart."""
        async with self.lock:
            watches, self.watches = list(self.watches.values()), {}
        for watch in watches:
            await _halt(watch)

    async def _run(self, watch: Watch, git: Git) -> None:
        try:
            async for paths in self.source(git, watch.mode, self.settings.watch_debounce_ms, watch.stop):
                await self.trigger(watch, paths)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            watch.last_outcome = f"ended: {log.redact(str(err))}"
            log.event(
                self.logger,
                "watch.ended",
                level=logging.ERROR,
                criterion=watch.criterion_id,
                error=type(err).__name__,
                reason=str(err),
            )

    async def trigger(self, watch: Watch, paths: Iterable[str] = ()) -> Outcome:
        """Handle one trigger; never raises: a failure is its outcome, logged, and the watch goes on."""
        git = Git(watch.repo)
        try:
            result = await (self._commit(watch, git) if watch.mode == "commit" else self._files(watch, git))
        except (CatalystCliError, GitError, OSError) as err:
            result = Outcome("failed", str(err))
        except Exception as err:
            result = Outcome("failed", f"{type(err).__name__}: {err}")
        watch.last_trigger, watch.last_outcome = _now(), result.outcome
        failed = result.outcome in ("failed", "refused")
        log.event(
            self.logger,
            "watch.trigger",
            level=logging.WARNING if failed else logging.INFO,
            criterion=watch.criterion_id,
            mode=watch.mode,
            actor=watch.actor.describe(),
            paths=sorted(paths)[:SHOWN],
            commits=list(result.commits[:SHOWN]),
            adopted=list(result.adopted),
            outcome=result.outcome,
            reason=result.reason,
        )
        return result

    async def _files(self, watch: Watch, git: Git) -> Outcome:
        watch.diff = await git.working_tree()
        return Outcome("diff refreshed", f"{len(watch.diff)} changed file(s)")

    async def _commit(self, watch: Watch, git: Git) -> Outcome:
        head = await git.head()
        if head is None or head == watch.last_commit:
            return Outcome("no new commit")
        base = watch.last_commit
        found = await self.cli.unrecorded(watch.repo, f"{base}..{head}" if base else head)
        watch.diff = await git.changed(base, head)
        local = await git.local_commits(base, head) if found else set()
        commits = tuple(
            {
                "commit": c.get("commit"),
                "author": c.get("author"),
                "subject": c.get("subject"),
                "pushed": c.get("commit") not in local,
            }
            for c in found
        )
        todo = [c for c in found if c.get("commit") in local]
        if not todo:
            watch.last_commit = head
            pushed = len(found) - len(todo)
            why = f"{pushed} pushed commit(s) not adopted: they came from elsewhere" if pushed else ""
            return Outcome("nothing to adopt", why, commits)
        try:
            authorized = await self.gate.authorize(
                PinnedResolver(self.resolver, watch.actor), Action.CRITERION_UPDATE, watch.criterion_id
            )
        except DeniedError as err:
            return Outcome("refused", err.reason, commits)
        adopted = await self._adopt(authorized, watch, todo)
        watch.last_commit = head
        return Outcome("adopted", "", commits, adopted)

    async def _adopt(
        self, authorized: AuthorizedAction, watch: Watch, commits: Sequence[dict[str, Any]]
    ) -> tuple[str, ...]:
        """Adopt each commit as the authorized actor, with its own subject as the intent (mcp-WATCH-000002)."""
        authorized.require(Action.CRITERION_UPDATE, watch.criterion_id)
        adopted = []
        for c in commits:
            sha = str(c["commit"])
            intent = str(c.get("subject") or "").strip() or f"commit {sha[:10]}"
            await self.cli.adopt(watch.repo, sha, intent=intent, actor=authorized.actor.username)
            adopted.append(sha)
        return tuple(adopted)


async def _halt(watch: Watch) -> None:
    watch.stop.set()
    if watch.task is not None:
        watch.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watch.task


def _project_name(repo: Path) -> str | None:
    try:
        with (repo / POINTER).open("rb") as fh:
            name = tomllib.load(fh).get("project_name")
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return None
    return name if isinstance(name, str) else None
