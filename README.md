# catalyst-mcp

An MCP server that keeps catalyst criteria in step with the git repositories they mirror: watchers, criteria,
templates and users. Every write is authorized against the acting identity in the identity provider, and refused
by default. It orchestrates the `catalyst` command line and
[catalyst-users](https://github.com/cantica-tech/catalyst-users); see [docs/design.md](docs/design.md).

Status: 0.1.0 is the skeleton. Every tool is declared, and each says it is not implemented yet.

## Run

```sh
uv sync
uv run catalyst-mcp                                  # stdio
uv run catalyst-mcp --transport streamable-http      # http://127.0.0.1:8765/mcp
```

Settings are `CATALYST_MCP_*` environment variables ([settings.py](src/catalyst_mcp/settings.py)). The Keycloak ones
only override catalyst-users' own settings (`catalyst-users setup`).

## Develop

```sh
uv sync
uv run pre-commit install
uv run ruff check && uv run ruff format --check && uvx pyright --pythonpath .venv/bin/python && uv run pytest
```
