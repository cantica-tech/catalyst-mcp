"""Settings: `CATALYST_MCP_*` environment variables over the defaults (pydantic-settings).

The Keycloak settings are overrides. Unset, catalyst-users' own settings decide (its `IAM/settings.json`, written by
`catalyst-users setup`), so a machine catalyst already signs on needs none of them. catalyst-users holds no admin
secret and acts with the caller's own token; the one secret here is the API client's, which the HTTP transport
needs to introspect a caller's token.

catalyst is found from one setting (mcp-ARCH-000003): `CATALYST_MCP_CATALYST_ROOT`, a catalyst checkout or release,
gives the command (`dist/catalyst.pyz`), the kernel (`framework/kernel`) and a module beside it
(`catalyst-<module>`); each of them can still be set on its own, and wins.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal

from catalyst_users import config
from catalyst_users.errors import CatalystUsersError
from pydantic import Field, SecretStr, model_validator
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

    catalyst_root: Path | None = None  # a catalyst checkout or release: the defaults of the next three
    # how to run catalyst; in the environment a JSON list, e.g. '["python3", "/path/to/catalyst.pyz"]'
    catalyst_cli: list[str] = Field(default_factory=lambda: ["catalyst"])
    catalyst_kernel: Path | None = None  # `catalyst init --kernel`; None: the catalyst command's own
    catalyst_home: Path | None = None  # None: CATALYST_HOME, else ~/.catalyst (where catalyst keeps criteria)
    projects_root: Path = Field(default_factory=lambda: Path.home() / "catalyst-mcp" / "projects")
    criterion_module: str = "software-engineering"
    criterion_module_dir: Path | None = None  # `catalyst init --module-dir`; None: catalyst searches for it
    criterion_agent: str | None = None  # `catalyst init --agent`, recorded in catalyst.toml
    identity_issuer: str | None = None  # `catalyst init --identity-issuer`; None: catalyst-users' issuer
    templates_dir: Path | None = None  # None: the templates catalyst-mcp ships
    watch_debounce_ms: int = Field(default=500, ge=0)

    log_level: LogLevel = "INFO"
    log_format: LogFormat = "json"

    @model_validator(mode="after")
    def _from_catalyst_root(self) -> Settings:
        if self.catalyst_root is None:
            return self
        root = self.catalyst_root.expanduser()
        pyz, kernel = root / "dist" / "catalyst.pyz", root / "framework" / "kernel"
        missing = [str(path) for path in (pyz, kernel) if not path.exists()]
        if missing:
            raise ValueError(f"catalyst_root {root} is no catalyst checkout or release: no {' and no '.join(missing)}")
        given = self.model_fields_set
        if "catalyst_cli" not in given:
            self.catalyst_cli = [sys.executable, str(pyz)]
        if "catalyst_kernel" not in given:
            self.catalyst_kernel = kernel
        sibling = root.parent / f"catalyst-{self.criterion_module}"
        if "criterion_module_dir" not in given and (sibling / "module.yaml").is_file():
            self.criterion_module_dir = sibling
        return self

    def signing_issuer(self) -> str | None:
        """The issuer `catalyst init` signs a new criterion through: the setting, else catalyst-users' own, else
        none (catalyst decides)."""
        if self.identity_issuer:
            return self.identity_issuer
        try:
            return config.load(self.users_overrides()).issuer
        except CatalystUsersError:
            return None

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
