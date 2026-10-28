# catalyst-mcp design

An MCP server that keeps a catalyst criterion in step with the git repository it mirrors. It **orchestrates** and
does not reimplement: criteria are written through the `catalyst` command line, people, groups and roles through
[catalyst-users](https://github.com/cantica-tech/catalyst-users). What catalyst-mcp owns is the watcher, the
authorization gate every write goes through, its audit log, and the tool surface.

## Decisions (2026-10-28)

| # | Decision | Why |
| --- | --- | --- |
| D1 | Orchestrate catalyst and catalyst-users; no criterion engine of its own | catalyst already journals, signs and serves criteria; its core is standard-library only, so the watcher's dependencies cannot live in it |
| D2 | A criterion is a **group** in one realm (`/criteria/<name>`), not a realm of its own | one person, one account and single sign-on across criteria; no server-admin credential; the group name is as unique as a realm name. This is catalyst-users' model already |
| D3 | IAM through catalyst-users, used as a library | catalyst signs through it; it acts with the caller's own token and holds no admin secret |
| D4 | Repository `cantica-tech/catalyst-mcp`, on the cantica-tech standard (`.standards/`, ruff 0.16.10) | |
| D5 | `catalyst journal adopt` must sign with the verified identity, never the git author: catalyst BUG-000044 | the watcher adopts hand-made commits and needs a trustworthy actor |

`delete_user` disables a person and never deletes them: what they signed keeps naming them (catalyst's L7).

## The hard rule: every write is authorized

- `auth.gate.authorize(actor, action, criterion_id)` is the only way to get an `AuthorizedAction`, and every
  mutating service method requires one. The type makes a bypass impossible to write; a test also scans the services
  for writes that skip it.
- **Deny by default.** `auth/permissions.py` defines `Permissions.can(actor, action, criterion_id) -> Decision`; the
  default is `DenyAll` until the real role mapping arrives. With it, no criterion write can succeed — intended.
- Every decision is logged: `authz.decision` with `actor` (issuer and subject), `criterion`, `action`, `decision`,
  `reason`. Logs are redacted (`log.py`): no token, secret or password is ever written.
- **The actor.** Over stdio, the identity catalyst-users says is logged in, else the machine's system account under
  catalyst's rules (fw-IDENTITY-000002). Over HTTP, the bearer token, introspected at the provider. A watch acts as
  the identity that started it; if that identity cannot be resolved when the watch triggers, the update is
  denied. A commit's git author is recorded but never authorizes anything.

## Watchers

- `file_change`: `watchfiles` on the working tree (`.git/` and ignored files excluded), debounced.
- `commit`: `watchfiles` on `.git/HEAD` and `.git/refs/`, so nothing is installed in the project (a project holds
  only `catalyst.toml`). A pre-push gate, if wanted, is a user-level `core.hooksPath`, wired once per machine.
- On trigger: diff from the last processed commit (git command line, async) → `authorize` → update through the
  catalyst command line → audit record.

## Atomic create

Validate the rules (`criterion/schema.py`) → create the criterion's group → `catalyst init` the criterion → add its
members. Each step registers an undo, and a failure runs them in reverse. Creating a criterion that exists with the
same owner changes nothing; with another owner it raises `CriterionExists`.

## Pending inputs (not invented)

- The criterion JSON schema: `criterion/schema.py` holds a `CriterionSchema` protocol and a stub (`id`, `name`,
  `rules: dict`); nothing else assumes its shape.
- The Keycloak roles and their mapping: `auth/permissions.py`, `DenyAll` until then. The likely source is
  catalyst-users' `keycloak/realm-catalyst.json` and `catalyst-users roles provision`; to be confirmed.

## The knowledge-base server (to be detailed)

After a criterion changes, the why and the how of the code change reach a central catalyst knowledge base, each
change carrying its signer.

- **Signed, not tokened.** A change carries catalyst's signer record (`iss`, `sub`, username, time); the server
  verifies the bearer token of whoever sends it and refuses an entry signed by anyone else, as `catalyst serve`
  does today. A raw token is never stored: it expires and it is a secret. Verifying later without the server
  would need a detached signature (JWS), behind the same interface.
- **Tenancy: one server, many spaces, one realm.** Neither one server per project nor realms inside the KB server.
  A KB server serves an organization (one realm). It is divided into **spaces**, each grouping one or more
  criteria: a catalyst workspace, which already groups projects, maps to one space; a project outside any
  workspace is a space of its own. Access to a space is membership of a group (`/spaces/<name>`) in the same realm,
  and a person sees a space's knowledge only through criteria they belong to. Each space keeps its own index and
  storage, so isolation lies in the data, not in the identity provider. An organization that needs hard isolation
  runs its own KB server against its own realm; nothing in the design changes.
- `catalyst serve` handles one criterion per database today, so the KB server is a new service that reads the
  criteria's served journals (`GET journal?since=`), not a mode of `catalyst serve`.

## Layout

```text
src/catalyst_mcp/
  server.py            the MCP server; stdio by default, --transport streamable-http
  settings.py          CATALYST_MCP_* (pydantic-settings)
  log.py               structured, redacted logging on standard error
  tools/               thin tool functions: watch, criteria, templates, users
  auth/                actor.py, permissions.py (DenyAll), gate.py
  iam/                 the catalyst-users wrapper (async over its standard-library core)
  criterion/           schema.py (stub), service.py, catalyst_cli.py
  watch/               manager.py, triggers.py, diff.py
  templates/           loader.py
templates/             versioned JSON: <name>/<version>.json
tests/                 unit tests with the provider mocked; one Keycloak testcontainer test (marker `integration`)
```

## Implementation

1. Settings and the server skeleton, every tool's signature — done in 0.1.0.
2. The catalyst-users wrapper (`iam/`): people, roles, criteria and their members, off the event loop — done
   (REQ-000001). A criterion's membership is only read there: catalyst changes it and records each change.
   catalyst-users cannot change a person's email or name yet, so `update_user` can only enable or disable.
3. The auth layer (`auth/`): actor resolution (session, system account, HTTP token, a watch's pinned identity),
   `permissions.py` with `DenyAll`, the gate and its audit log, the HTTP token verifier — done (REQ-000002). The
   services require an `AuthorizedAction` as they land; wiring the verifier into the HTTP transport is step 7.
4. The criterion service, on the stub schema.
5. The watcher and git diff, authorizing through the gate.
6. The templates loader, format-agnostic until the schema lands.
7. The final tool wiring.

Each step ends with `ruff check`, `ruff format --check`, `pyright` and `pytest`.
