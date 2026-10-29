import shutil
import sys
from pathlib import Path

import pytest
from catalyst_users.config import NoSettingsError

from catalyst_mcp.settings import Settings


def test_defaults_leave_keycloak_to_catalyst_users(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("KEYCLOAK_URL", "KEYCLOAK_REALM", "TRANSPORT"):
        monkeypatch.delenv(f"CATALYST_MCP_{name}", raising=False)
    settings = Settings()
    assert settings.transport == "stdio"
    assert settings.http_host == "127.0.0.1"
    assert settings.catalyst_cli == ["catalyst"]
    assert settings.users_overrides() == {}


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CATALYST_MCP_KEYCLOAK_URL", "https://id.example.org")
    monkeypatch.setenv("CATALYST_MCP_KEYCLOAK_REALM", "cantica")
    monkeypatch.setenv("CATALYST_MCP_KEYCLOAK_API_CLIENT_SECRET", "s3cret")
    monkeypatch.setenv("CATALYST_MCP_CATALYST_CLI", '["python3", "catalyst.pyz"]')
    monkeypatch.setenv("CATALYST_MCP_USERS_POINTER", "/tmp/pointer.json")
    settings = Settings()
    assert settings.catalyst_cli == ["python3", "catalyst.pyz"]
    assert settings.users_pointer == Path("/tmp/pointer.json")
    assert settings.users_overrides() == {
        "server_url": "https://id.example.org",
        "realm": "cantica",
        "api_client_secret": "s3cret",
    }


def test_the_secret_never_shows_in_its_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CATALYST_MCP_KEYCLOAK_API_CLIENT_SECRET", "s3cret")
    settings = Settings()
    assert "s3cret" not in repr(settings)
    assert "s3cret" not in settings.model_dump_json()


def test_a_port_out_of_range_is_refused() -> None:
    with pytest.raises(ValueError, match="http_port"):
        Settings(http_port=70000)


# --- catalyst from one setting (mcp-ARCH-000003) ---------------------------------------------------------------


def checkout(tmp_path: Path, module: bool = True) -> Path:
    root = tmp_path / "catalyst"
    (root / "dist").mkdir(parents=True)
    (root / "dist" / "catalyst.pyz").write_text("")
    (root / "framework" / "kernel").mkdir(parents=True)
    if module:
        (tmp_path / "catalyst-software-engineering").mkdir()
        (tmp_path / "catalyst-software-engineering" / "module.yaml").write_text("id: software-engineering\n")
    return root


def test_the_catalyst_root_gives_the_command_the_kernel_and_the_module(tmp_path: Path) -> None:
    root = checkout(tmp_path)
    settings = Settings(catalyst_root=root)
    assert settings.catalyst_cli == [sys.executable, str(root / "dist" / "catalyst.pyz")]
    assert settings.catalyst_kernel == root / "framework" / "kernel"
    assert settings.criterion_module_dir == tmp_path / "catalyst-software-engineering"


def test_a_module_not_beside_the_root_is_left_to_catalyst(tmp_path: Path) -> None:
    settings = Settings(catalyst_root=checkout(tmp_path, module=False))
    assert settings.criterion_module_dir is None


def test_each_explicit_setting_wins_over_the_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = checkout(tmp_path)
    monkeypatch.setenv("CATALYST_MCP_CATALYST_ROOT", str(root))
    monkeypatch.setenv("CATALYST_MCP_CATALYST_CLI", '["catalyst"]')
    settings = Settings(catalyst_kernel=tmp_path / "k", criterion_module_dir=tmp_path / "m")
    assert settings.catalyst_cli == ["catalyst"]
    assert (settings.catalyst_kernel, settings.criterion_module_dir) == (tmp_path / "k", tmp_path / "m")


@pytest.mark.parametrize("gone", ["dist/catalyst.pyz", "framework/kernel"])
def test_a_root_without_catalyst_is_refused_naming_what_is_missing(tmp_path: Path, gone: str) -> None:
    root = checkout(tmp_path)
    target = root / gone
    shutil.rmtree(target) if target.is_dir() else target.unlink()
    with pytest.raises(ValueError, match=f"no catalyst checkout or release: no {target}"):
        Settings(catalyst_root=root)


def test_init_signs_through_the_setting_else_catalyst_users_issuer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert Settings(identity_issuer="https://id/realms/x").signing_issuer() == "https://id/realms/x"
    monkeypatch.setenv("CATALYST_USERS_HOME", str(tmp_path / "users"))
    settings = Settings(keycloak_url="http://id.example", keycloak_realm="r")
    assert settings.signing_issuer() == "http://id.example/realms/r"
    monkeypatch.setattr("catalyst_mcp.settings.config.load", _no_settings)
    assert Settings().signing_issuer() is None


def _no_settings(*_: object) -> None:
    raise NoSettingsError()
