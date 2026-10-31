"""A watch adopting a hand-made commit with the real catalyst command line, in a throwaway catalyst home. Opt-in, as
`test_criterion_catalyst.py`: CATALYST_MCP_IT_CATALYST. The actor is resolved as over stdio (the identity logged in on
this machine, else its system account): catalyst signs only as an identity it verifies itself (catalyst BUG-000044)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from catalyst_mcp.auth import Action, Gate, SessionResolver
from catalyst_mcp.criterion import CatalystCli, CriterionService
from catalyst_mcp.iam import CatalystUsersDirectory
from catalyst_mcp.settings import Settings
from catalyst_mcp.watch import WatchManager

sys.path.insert(0, str(Path(__file__).parent.parent))
from test_criterion import Allow, FakeDirectory, authorized
from test_watch import Fixed, commit, git

CATALYST = os.environ.get("CATALYST_MCP_IT_CATALYST")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not CATALYST, reason="needs CATALYST_MCP_IT_CATALYST"),
]


async def test_a_watch_adopts_a_hand_made_commit_as_its_actor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalyst = Path(CATALYST or "")
    monkeypatch.setenv("CATALYST_USERS_HOME", str(Path.home() / ".catalyst" / "users"))
    settings = Settings(
        catalyst_root=catalyst,
        catalyst_home=tmp_path / "home",
        projects_root=tmp_path / "projects",
        watch_debounce_ms=50,
    )
    cli = CatalystCli.from_settings(settings)
    actor = await SessionResolver(CatalystUsersDirectory.from_settings(settings)).resolve()
    await CriterionService(cli, FakeDirectory(), settings).create(  # type: ignore[arg-type]
        await authorized("it-watch"), "it-watch", {}, actor.username
    )
    repo = (settings.projects_root / "it-watch").resolve()
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "chore: catalyst")

    watches = WatchManager(Gate(Allow()), Fixed(actor), cli, settings)
    watch, _ = await watches.start(
        await Gate(Allow()).authorize(actor, Action.WATCH_START, "it-watch"), repo, "it-watch"
    )
    sha = commit(repo, "app.py", "feat: written by hand")
    try:
        result = await watches.trigger(watch)
    finally:
        await watches.aclose()
    assert result.outcome == "adopted" and result.adopted == (sha,), result.reason
    env = {**os.environ, "CATALYST_HOME": str(settings.catalyst_home)}
    left = subprocess.run(
        [*settings.catalyst_cli, "unrecorded", "--json"], cwd=repo, env=env, capture_output=True, text=True, check=False
    )
    assert left.stdout.strip() == "[]", left.stdout + left.stderr
    shown = subprocess.run(
        [*settings.catalyst_cli, "journal", "show", "--json"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert sha in shown.stdout and f'"{actor.username}"' in shown.stdout, shown.stdout[-2000:] + shown.stderr
