"""Settings: `CATALYST_MCP_*` environment variables over the defaults (pydantic-settings).

The Keycloak settings are overrides. Unset, catalyst-users' own settings decide (its `IAM/settings.json`, written by
`catalyst-users setup`), so a machine catalyst already signs on needs none of them. catalyst-users holds no admin
secret and acts with the caller's own token; the one secret here is the API client's, which the HTTP transport
needs to introspect a caller's token.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Transport = Literal["stdio", "streamable-http"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
LogFormat = Literal["json", "console"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CATALYST_MCP_", extra="ignore")

    transport: Transport = "stdio"
    http_host: str = "127.0.0.1"
    http_port: int = Field(default=8765, ge=1, le=65535)
    http_path: str = "/mcp"

    keycloak_url: str | None = None
    keycloak_realm: str | None = None
    keycloak_client_id: str | None = None
    keycloak_api_client_id: str | None = None
    keycloak_api_client_secret: SecretStr | None = None
    users_pointer: Path | None = None  # catalyst-users' --pointer: which provider it is a facade to

    # how to run catalyst; in the environment a JSON list, e.g. '["python3", "/path/to/catalyst.pyz"]'
    catalyst_cli: list[str] = Field(default_factory=lambda: ["catalyst"])
    catalyst_kernel: Path | None = None  # `catalyst init --kernel`; None: the catalyst command's own
    catalyst_home: Path | None = None  # None: CATALYST_HOME, else ~/.catalyst (where catalyst keeps criteria)
    projects_root: Path = Field(default_factory=lambda: Path.home() / "catalyst-mcp" / "projects")
    criterion_module: str = "software-engineering"
    criterion_module_dir: Path | None = None  # `catalyst init --module-dir`; None: catalyst searches for it
    criterion_agent: str | None = None  # `catalyst init --agent`, recorded in catalyst.toml
    identity_issuer: str | None = None  # `catalyst init --identity-issuer`; None: this machine's catalyst-users
    templates_dir: Path | None = None  # None: the templates catalyst-mcp ships
    watch_debounce_ms: int = Field(default=500, ge=0)

    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"

    def criterion_home(self, name: str) -> Path:
        """Where catalyst keeps the criterion `name` (`$CATALYST_HOME/projects/<name>/criterion`)."""
        home = self.catalyst_home or Path(os.environ.get("CATALYST_HOME") or Path.home() / ".catalyst")
        return home / "projects" / name / "criterion"

    def users_overrides(self) -> dict[str, Any]:
        """The catalyst-users settings this server overrides, as `catalyst_users.config.load` takes them."""
        secret = self.keycloak_api_client_secret
        pairs = {
            "server_url": self.keycloak_url,
            "realm": self.keycloak_realm,
            "client_id": self.keycloak_client_id,
            "api_client_id": self.keycloak_api_client_id,
            "api_client_secret": secret.get_secret_value() if secret else None,
        }
        return {key: value for key, value in pairs.items() if value is not None}
