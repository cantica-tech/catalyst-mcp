# catalyst-mcp

An MCP server that keeps catalyst criteria in step with the git repositories they mirror: watchers, criteria,
templates and users. Every write is authorized against the acting identity in the identity provider, and refused
by default. It orchestrates the `catalyst` command line and
[catalyst-users](https://github.com/cantica-tech/catalyst-users); see [docs/design.md](docs/design.md).

Status: every tool works over stdio; over streamable HTTP the server only reads (catalyst would sign as the server,
not the caller). The realm role `catalyst-admin` creates criteria and manages people; a criterion's admins update it
and watch it.

## Run

```sh
uv sync
uv run catalyst-mcp                                  # stdio
uv run catalyst-mcp --transport streamable-http      # http://127.0.0.1:8765/mcp
```

Settings are `CATALYST_MCP_*` environment variables ([settings.py](src/catalyst_mcp/settings.py)). The Keycloak ones
only override catalyst-users' own settings (`catalyst-users setup`); log in with `catalyst-users login`.

One setting finds catalyst: `CATALYST_MCP_CATALYST_ROOT`, a catalyst checkout or release. From it, catalyst-mcp runs
`dist/catalyst.pyz`, passes `catalyst init` the `framework/kernel`, and finds the module beside it
(`catalyst-<module>`); a new criterion signs through catalyst-users' issuer. In Claude Code, at user level:

```sh
claude mcp add-json -s user catalyst-mcp '{"type":"stdio","command":"uv",
  "args":["run","--directory","<catalyst-mcp checkout>","catalyst-mcp"],
  "env":{"CATALYST_MCP_CATALYST_ROOT":"<catalyst checkout>"}}'
```

## Develop

```sh
uv sync
uv run pre-commit install
uv run ruff check && uv run ruff format --check && uvx pyright --pythonpath .venv/bin/python && uv run pytest
```
