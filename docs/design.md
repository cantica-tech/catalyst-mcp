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
  default is `RoleMapping` (mcp-AUTHZ-000005, the owner's decision of 2026-10-29): the realm role `catalyst-admin`
  creates criteria and manages people and roles; a criterion's admins, asked of the identity provider at the moment
  of the decision, update it and start and stop its watches. Anything else is refused.
- Every decision is logged: `authz.decision` with `actor` (issuer and subject), `criterion`, `action`, `decision`,
  `reason`. Logs are redacted (`log.py`): no token, secret or password is ever written.
- **The actor.** Over stdio, the identity catalyst-users says is logged in, else the machine's system account under
  catalyst's rules (fw-IDENTITY-000002). Over HTTP, the bearer token, introspected at the provider. A watch acts as
  the identity that started it; if that identity cannot be resolved when the watch triggers, the update is
  denied. A commit's git author is recorded but never authorizes anything.
- **Read-only over HTTP** (the owner's decision of 2026-10-29, mcp-AUTHZ-000004). The caller is verified by its
  token, but catalyst would sign and catalyst-users would act as the server's own identity. So over HTTP
  `StdioOnly` refuses every write and every read of people, logged as any decision; templates and the list of
  watches stay readable. Over stdio everything works. To revisit once catalyst can sign for a verified caller.

## Watchers

- `file_change`: `watchfiles` on the working tree (`.git/` and ignored files excluded), debounced.
- `commit`: `watchfiles` on `.git/HEAD` and `.git/refs/`, so nothing is installed in the project (a project holds
  only `catalyst.toml`). A pre-push gate, if wanted, is a user-level `core.hooksPath`, wired once per machine.
- On a commit: the diff from the last processed commit (git, async) and catalyst's list of the commits since then
  with changes not in the journal (`catalyst unrecorded`), kept to those made here and not yet pushed (a commit a
  remote-tracking branch holds came from elsewhere) → `authorize` `criterion.update` as the identity verified now,
  which must be the one that started the watch → `catalyst journal adopt --as <that identity>`, the commit's subject
  as the intent → a `watch.trigger` record. Refused or failed, nothing is written and the next trigger tries again; a
  commit catalyst already records needs no update and no authorization.
- On a saved file: the working tree's diff against `HEAD` is refreshed (shown by `list_watches`); nothing is written,
  since catalyst journals commits, not uncommitted work.
- A watch writes only the local journal: it never pushes and never answers catalyst's request for assent. Watches
  live as long as the server.

## Atomic create

Validate the rules (`criterion/schema.py`) → create the criterion's group → `catalyst init` the criterion → add its
members. Each step registers an undo, and a failure runs them in reverse. Creating a criterion that exists with the
same owner changes nothing; with another owner it raises `CriterionExists`.

## Finding catalyst (2026-10-29)

`catalyst init` runs only from catalyst's `catalyst.pyz` (the `~/.catalyst/bin` launcher runs a project's own
catalyst, and a new repository has none), needs the kernel and the module named, and signs as the system account
unless told the issuer. One setting, `CATALYST_MCP_CATALYST_ROOT`, gives the first three; the issuer is catalyst-users'
own (mcp-ARCH-000003, REQ-000008). Found in the first live trial.

## Pending inputs (not invented)

- The criterion JSON schema — given 2026-10-30, adopted as v1 to be extended in later versions (REQ-000009):
  `rules` may hold `naming` (`max_length` an integer) and `branches` (a list of strings). `criterion/schema.py` keeps
  every version behind the `CriterionSchema` protocol; a criterion kept under the stub (v0) stays valid. The rules are
  kept as JSON, not mapped to catalyst rules.
- The Keycloak roles and their mapping — given 2026-10-29: `RoleMapping` (REQ-000007), over the realm's existing
  `catalyst-admin` role and catalyst-users' criterion admins.

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
  services.py          what the tools share: directory, gate, the transport's actor, the services
  settings.py          CATALYST_MCP_* (pydantic-settings)
  log.py               structured, redacted logging on standard error
  tools/               thin tool functions: watch, criteria, templates, users
  auth/                actor.py, permissions.py (RoleMapping, StdioOnly, DenyAll), gate.py
  iam/                 the catalyst-users wrapper (async over its standard-library core)
  criterion/           schema.py (stub), service.py, catalyst_cli.py
  watch/               manager.py, triggers.py, diff.py
  templates/           loader.py; catalog/<name>/<version>.json, the shipped templates (versioned JSON)
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
4. The criterion service (`criterion/`), on the stub schema — done (REQ-000003). The owner's decisions of
   2026-10-29: the group last and safe to repeat (catalyst-users never deletes a group); a criterion without a
   repository gets a new one under `CATALYST_MCP_PROJECTS_ROOT`; the rules JSON is kept in the criterion
   (`catalyst-mcp/criterion.json`), not mapped to catalyst rules; the module is `CATALYST_MCP_CRITERION_MODULE`.
   Members are refused for a criterion not served through `catalyst serve`: catalyst records members only there.
5. The watcher and git diff, authorizing through the gate — done (REQ-000004). The owner's decisions of
   2026-10-29: a commit trigger adopts as the verified actor; a file-change trigger refreshes the diff and writes
   nothing. Until catalyst BUG-000044 is fixed, catalyst records `--as` as given: catalyst-mcp passes only the
   identity the gate verified.
6. The templates loader, format-agnostic until the schema lands — done (REQ-000005). A template fills in
   `create_criterion`'s arguments (`name`, `rules`, `owner`, `users`) with `${param}` placeholders and is created
   exactly as `create_criterion` creates. The shipped catalog is `src/catalyst_mcp/templates/catalog/`, inside the
   package so a wheel carries it; `CATALYST_MCP_TEMPLATES_DIR` names another directory of the same layout.
7. The final tool wiring — done (REQ-000006). `services.py` holds what the tools share; each writing tool asks the
   gate, then its service; failures are tool errors naming their cause. Over HTTP the token verifier guards every
   request and the server only reads. The watches stop with the server.

Each step ends with `ruff check`, `ruff format --check`, `pyright` and `pytest`.
