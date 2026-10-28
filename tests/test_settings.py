from pathlib import Path

import pytest

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
