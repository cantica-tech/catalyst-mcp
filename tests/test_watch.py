import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from catalyst_users.provider import Whoami

from catalyst_mcp import log
from catalyst_mcp.auth import Action, Actor, AuthorizationMismatchError, Decision, DenyAll, Gate
from catalyst_mcp.criterion import CatalystCli, CatalystCliError
from catalyst_mcp.settings import Settings
from catalyst_mcp.watch import Git, WatchManager, WatchRefusedError, triggers

ALICE = Actor.from_identity(
    Whoami(issuer="https://id/realms/c", subject="sub-alice", username="alice", name="A"), "session"
)
BOB = Actor.from_identity(Whoami(issuer="https://id/realms/c", subject="sub-bob", username="bob", name="B"), "session")


class Allow:
    async def can(self, actor: Actor, action: Action, criterion_id: str | None) -> Decision:
        return Decision.allow("test")


class Fixed:
    """Whoever the trigger-time resolver verifies."""

    def __init__(self, actor: Actor) -> None:
        self.actor = actor

    async def resolve(self) -> Actor:
        return self.actor


class FakeCli:
    """catalyst's `unrecorded` and `adopt`: every commit is unrecorded until adopted."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.adopted: list[tuple[str, str, str]] = []  # commit, intent, actor
        self.fail: Exception | None = None

    async def unrecorded(self, repo: Path, revisions: str) -> list[dict[str, Any]]:
        out = git(repo, "log", "--reverse", "--format=%H%x00%an%x00%s", revisions).splitlines()
        done = {sha for sha, _, _ in self.adopted}
        found = [line.split("\0") for line in out]
        return [{"commit": s, "author": a, "subject": m, "files": []} for s, a, m in found if s not in done]

    async def adopt(self, repo: Path, commit: str, *, intent: str, actor: str) -> list[dict[str, Any]]:
        if self.fail:
            raise self.fail
        self.adopted.append((commit, intent, actor))
        return [{"commit": commit, "actor": actor}]


def git(repo: Path, *args: str) -> str:
    who = ["-c", "user.name=Hand Maker", "-c", "user.email=hand@example.org", "-c", "commit.gpgsign=false"]
    return subprocess.run(["git", *who, *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def commit(repo: Path, name: str, subject: str) -> str:
    (repo / name).write_text(subject)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", subject)
    return git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "proj"
    path.mkdir()
    git(path, "init", "-q", "-b", "main")
    (path / "catalyst.toml").write_text('project_name = "c1"\n')
    (path / ".gitignore").write_text("build/\n")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "chore: catalyst")
    return path.resolve()


def manager(cli: FakeCli, permissions: Any = None, verified: Actor = ALICE) -> WatchManager:
    async def nothing(*_: Any):
        await anyio.sleep_forever()
        yield frozenset()

    gate = Gate(permissions or Allow())
    return WatchManager(gate, Fixed(verified), cli, Settings(), source=nothing)  # type: ignore[arg-type]


async def authorized(action: Action, criterion: str = "c1", actor: Actor = ALICE):
    return await Gate(Allow()).authorize(actor, action, criterion)


@pytest.fixture
def records(capsys: pytest.CaptureFixture[str]):
    log.configure("INFO", "json")

    def read(event: str, action: str | None = None) -> list[dict[str, Any]]:
        lines = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")]
        return [r for r in lines if r.get("event") == event and action in (None, r.get("action"))]

    return read


# --- start and stop (mcp-WATCH-000001) --------------------------------------------------------------------------


async def test_a_watch_starts_only_with_its_authorization(repo: Path) -> None:
    watches = manager(FakeCli(repo))
    with pytest.raises(AuthorizationMismatchError):
        await watches.start(await authorized(Action.WATCH_STOP), repo, "c1")
    with pytest.raises(AuthorizationMismatchError):
        await watches.start(await authorized(Action.WATCH_START, "c2"), repo, "c1")
    assert watches.list() == []


async def test_only_a_repository_governed_by_the_criterion_is_watched(repo: Path, tmp_path: Path) -> None:
    watches = manager(FakeCli(repo))
    with pytest.raises(WatchRefusedError, match="governed by criterion c1, not by c2"):
        await watches.start(await authorized(Action.WATCH_START, "c2"), repo, "c2")
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(WatchRefusedError, match="not the absolute path of a git working tree"):
        await watches.start(await authorized(Action.WATCH_START), plain, "c1")
    git(plain, "init", "-q")
    with pytest.raises(WatchRefusedError, match="not governed by catalyst"):
        await watches.start(await authorized(Action.WATCH_START), plain, "c1")
    with pytest.raises(WatchRefusedError, match="absolute"):
        await watches.start(await authorized(Action.WATCH_START), "proj", "c1")
    assert watches.list() == []


async def test_a_watch_is_started_once_and_stopped_with_its_authorization(repo: Path, records) -> None:
    watches = manager(FakeCli(repo))
    watch, started = await watches.start(await authorized(Action.WATCH_START), repo, "c1")
    again, started_again = await watches.start(await authorized(Action.WATCH_START), repo, "c1")
    assert started and not started_again and again is watch
    with pytest.raises(WatchRefusedError, match="already watched"):
        await watches.start(await authorized(Action.WATCH_START, actor=BOB), repo, "c1")
    with pytest.raises(WatchRefusedError, match="already watched"):
        await watches.start(await authorized(Action.WATCH_START), repo, "c1", "file_change")
    [listed] = watches.list()
    assert listed["actor"]["sub"] == "sub-alice" and listed["running"] and listed["mode"] == "commit"
    with pytest.raises(AuthorizationMismatchError):
        await watches.stop(await authorized(Action.WATCH_START), "c1")
    assert await watches.stop(await authorized(Action.WATCH_STOP), "c1")
    assert not await watches.stop(await authorized(Action.WATCH_STOP), "c1")
    assert watches.list() == [] and watch.task is not None and watch.task.done()
    assert [r["event"] for r in records("watch.started")] == ["watch.started"]


# --- commit triggers (mcp-WATCH-000002, mcp-WATCH-000004) ------------------------------------------------------


async def test_a_hand_made_commit_is_adopted_as_the_identity_that_started_the_watch(repo: Path, records) -> None:
    cli = FakeCli(repo)
    watches = manager(cli)
    watch, _ = await watches.start(await authorized(Action.WATCH_START), repo, "c1")
    sha = commit(repo, "app.py", "feat: by hand")
    result = await watches.trigger(watch, {".git/refs/heads/main"})
    assert result.outcome == "adopted" and result.adopted == (sha,)
    assert cli.adopted == [(sha, "feat: by hand", "alice")]  # the verified identity, never the git author
    assert watch.last_commit == sha and [(c.path, c.status) for c in watch.diff] == [("app.py", "A")]
    [record] = records("watch.trigger")
    assert record["commits"][0]["author"] == "Hand Maker" and record["actor"]["username"] == "alice"
    assert (await watches.trigger(watch)).outcome == "no new commit"
    await watches.aclose()


async def test_a_refused_update_writes_nothing_and_is_tried_again(repo: Path, records) -> None:
    cli = FakeCli(repo)
    watches = manager(cli, DenyAll())
    watch, _ = await watches.start(await authorized(Action.WATCH_START), repo, "c1")
    start = watch.last_commit
    sha = commit(repo, "app.py", "fix: by hand")
    result = await watches.trigger(watch)
    assert result.outcome == "refused" and result.reason == DenyAll.REASON
    assert cli.adopted == [] and watch.last_commit == start
    [decision] = records("authz.decision", "criterion.update")
    assert decision["action"] == "criterion.update" and decision["decision"] == "deny"
    watches.gate.permissions = Allow()
    assert (await watches.trigger(watch)).adopted == (sha,)
    await watches.aclose()


async def test_another_verified_identity_is_refused(repo: Path) -> None:
    cli = FakeCli(repo)
    watches = manager(cli, verified=BOB)
    watch, _ = await watches.start(await authorized(Action.WATCH_START), repo, "c1")
    commit(repo, "app.py", "feat: by hand")
    result = await watches.trigger(watch)
    assert result.outcome == "refused" and "no longer the one verified (bob)" in result.reason
    assert cli.adopted == []
    await watches.aclose()


async def test_a_recorded_commit_needs_no_update_and_no_authorization(repo: Path, records) -> None:
    cli = FakeCli(repo)
    watches = manager(cli, DenyAll())
    watch, _ = await watches.start(await authorized(Action.WATCH_START), repo, "c1")
    sha = commit(repo, "app.py", "feat: through catalyst")
    cli.adopted.append((sha, "journaled", "alice"))  # catalyst already records it
    result = await watches.trigger(watch)
    assert result.outcome == "nothing to adopt" and watch.last_commit == sha
    assert records("authz.decision", "criterion.update") == []
    await watches.aclose()


async def test_a_pushed_commit_is_reported_never_adopted(repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    cli = FakeCli(repo)
    watches = manager(cli)
    watch, _ = await watches.start(await authorized(Action.WATCH_START), repo, "c1")
    pulled = commit(repo, "theirs.py", "feat: someone else's")
    git(repo, "push", "-q", "origin", "main")  # as a pull would: a remote-tracking branch holds it
    mine = commit(repo, "mine.py", "feat: mine")
    result = await watches.trigger(watch)
    assert result.adopted == (mine,) and cli.adopted[0][0] == mine
    assert {c["commit"]: c["pushed"] for c in result.commits} == {pulled: True, mine: False}
    await watches.aclose()


async def test_a_failure_is_the_outcome_and_the_watch_goes_on(repo: Path, records) -> None:
    cli = FakeCli(repo)
    cli.fail = CatalystCliError("journal", 1, "sent Bearer abc123 and broke")
    watches = manager(cli)
    watch, _ = await watches.start(await authorized(Action.WATCH_START), repo, "c1")
    start = watch.last_commit
    commit(repo, "app.py", "feat: by hand")
    result = await watches.trigger(watch)
    assert result.outcome == "failed" and watch.last_commit == start and "abc123" not in result.reason
    [record] = records("watch.trigger")
    assert record["outcome"] == "failed" and "abc123" not in json.dumps(record)
    cli.fail = None
    assert (await watches.trigger(watch)).outcome == "adopted"
    await watches.aclose()


async def test_an_update_needs_the_criterion_update_authorization(repo: Path) -> None:
    watches = manager(FakeCli(repo))
    watch, _ = await watches.start(await authorized(Action.WATCH_START), repo, "c1")
    with pytest.raises(AuthorizationMismatchError):
        await watches._adopt(await authorized(Action.CRITERION_UPDATE, "c2"), watch, [{"commit": "x"}])
    await watches.aclose()


# --- file-change triggers (mcp-WATCH-000003) -------------------------------------------------------------------


async def test_a_file_change_refreshes_the_diff_and_writes_nothing(repo: Path, records) -> None:
    cli = FakeCli(repo)
    watches = manager(cli)
    watch, _ = await watches.start(await authorized(Action.WATCH_START), repo, "c1", "file_change")
    assert watch.diff == []
    (repo / "draft.py").write_text("x")
    (repo / "catalyst.toml").write_text('project_name = "c1"\n# edited\n')
    (repo / "build").mkdir()
    (repo / "build" / "out.bin").write_text("ignored")
    result = await watches.trigger(watch, {"draft.py"})
    assert result.outcome == "diff refreshed"
    assert {(c.path, c.status) for c in watch.diff} == {("draft.py", "?"), ("catalyst.toml", "M")}
    assert cli.adopted == [] and records("authz.decision", "criterion.update") == []
    await watches.aclose()


# --- git and the real triggers ---------------------------------------------------------------------------------


async def test_git_reads_heads_changes_and_ignored_paths(repo: Path) -> None:
    g = Git(repo)
    first = await g.head()
    assert first and {c.path for c in await g.changed(None, first)} == {"catalyst.toml", ".gitignore"}
    sha = commit(repo, "a.txt", "a")
    assert [(c.path, c.status) for c in await g.changed(first, sha)] == [("a.txt", "A")]
    assert await g.ignored(["build/x", "a.txt"]) == {"build/x"} and await g.ignored([]) == set()
    assert await g.local_commits(first, sha) == {sha}
    own, common = await g.git_dirs()
    assert own == common == repo / ".git"


async def test_the_real_triggers_see_a_commit_and_a_saved_file(repo: Path) -> None:
    g = Git(repo)

    async def first(mode: Any, act: Any) -> frozenset[str]:
        stop = anyio.Event()
        source = triggers(g, mode, 50, stop)
        task = asyncio.ensure_future(anext(source))
        await asyncio.sleep(0.3)  # the watcher is up
        act()
        try:
            return await asyncio.wait_for(task, 10)
        finally:
            stop.set()

    seen = await first("commit", lambda: commit(repo, "b.txt", "b"))
    assert any(p == ".git/HEAD" or p.startswith(".git/refs/") for p in seen)
    seen = await first("file_change", lambda: (repo / "c.txt").write_text("c"))
    assert "c.txt" in seen and not any(p.startswith(".git") for p in seen)


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX shell script stands in for catalyst")
async def test_the_cli_reads_unrecorded_at_error_level_and_adopts(tmp_path: Path) -> None:
    fake = tmp_path / "catalyst"
    fake.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = unrecorded ]; then echo \'[{"commit": "abc", "author": "h", "subject": "s"}]\'; exit 1; fi\n'
        'echo "$@" > args.txt; echo \'{"commit": "abc", "actor": "alice"}\'\n'
    )
    fake.chmod(0o755)
    cli = CatalystCli([str(fake)])
    assert [c["commit"] for c in await cli.unrecorded(tmp_path, "a..b")] == ["abc"]
    assert await cli.adopt(tmp_path, "abc", intent="feat: x", actor="alice") == [{"commit": "abc", "actor": "alice"}]
    assert (tmp_path / "args.txt").read_text().split() == [
        "journal", "adopt", "abc", "--intent", "feat:", "x", "--as", "alice", "--json"
    ]  # fmt: skip
