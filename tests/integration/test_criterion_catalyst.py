"""A criterion created with the real catalyst command line, in a throwaway catalyst home; the identity provider's
group is faked. Opt-in: CATALYST_MCP_IT_CATALYST names a catalyst checkout (with `dist/catalyst.pyz` built and the
module checked out beside it, as catalyst-software-engineering); catalyst-users' settings must name a reachable
provider. Only the catalyst root is given: the command, the kernel, the module and the issuer are derived
(mcp-ARCH-000003)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from catalyst_mcp.criterion import CatalystCli, CriterionService
from catalyst_mcp.criterion.service import RULES_FILE
from catalyst_mcp.settings import Settings

sys.path.insert(0, str(Path(__file__).parent.parent))
from test_criterion import FakeDirectory, authorized

CATALYST = os.environ.get("CATALYST_MCP_IT_CATALYST")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not CATALYST, reason="needs CATALYST_MCP_IT_CATALYST"),
]


async def test_a_criterion_made_with_catalyst_passes_its_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalyst = Path(CATALYST or "")
    monkeypatch.setenv("CATALYST_USERS_HOME", str(Path.home() / ".catalyst" / "users"))
    settings = Settings(
        catalyst_root=catalyst,
        catalyst_home=tmp_path / "home",
        projects_root=tmp_path / "projects",
    )
    service = CriterionService(CatalystCli.from_settings(settings), FakeDirectory(), settings)  # type: ignore[arg-type]
    made = await service.create(await authorized("it-demo"), "it-demo", {"naming": {"max": 40}}, "alice")
    assert made.made == ("repository", "deployment", "rules", "group")
    assert (settings.criterion_home("it-demo") / RULES_FILE).is_file()
    check = subprocess.run(
        [*settings.catalyst_cli, "check"],
        cwd=settings.projects_root / "it-demo",
        env={**os.environ, "CATALYST_HOME": str(settings.catalyst_home)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    again = await service.create(await authorized("it-demo"), "it-demo", {"naming": {"max": 40}}, "alice")
    assert again.unchanged
