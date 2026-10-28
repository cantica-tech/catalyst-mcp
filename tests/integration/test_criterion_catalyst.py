"""A criterion created with the real catalyst command line, in a throwaway catalyst home; the identity provider's
group is faked. Opt-in: CATALYST_MCP_IT_CATALYST names a catalyst checkout (with `dist/catalyst.pyz` built), and
CATALYST_MCP_IT_MODULE_DIR the module's checkout; catalyst-users' settings must name a reachable provider."""

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
MODULE_DIR = os.environ.get("CATALYST_MCP_IT_MODULE_DIR")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not (CATALYST and MODULE_DIR), reason="needs CATALYST_MCP_IT_CATALYST and _MODULE_DIR"),
]


async def test_a_criterion_made_with_catalyst_passes_its_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalyst = Path(CATALYST or "")
    monkeypatch.setenv("CATALYST_USERS_HOME", str(Path.home() / ".catalyst" / "users"))
    settings = Settings(
        catalyst_cli=[sys.executable, str(catalyst / "dist" / "catalyst.pyz")],
        catalyst_home=tmp_path / "home",
        projects_root=tmp_path / "projects",
        catalyst_kernel=catalyst / "framework" / "kernel",
        criterion_module_dir=Path(MODULE_DIR or ""),
        identity_issuer=os.environ.get("CATALYST_MCP_IT_ISSUER", "http://localhost:8080/realms/catalyst"),
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
