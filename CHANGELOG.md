# Changelog

## 0.4.1 — 2026-10-31

- catalyst-users 0.10.0 (from 0.8.0); the locked dependencies upgraded. No change to any tool.
- The watch's integration test adopts as the identity resolved as over stdio (the one logged in, else the system
  account): since kernel 0.119.1 (catalyst BUG-000044) catalyst signs only as an identity it verifies itself.
- `docs/design.md`: steps 2 and 5 and D5 no longer say `update_user` cannot change details or that BUG-000044 is open.

## 0.4.0 — 2026-10-30

- `update_user` changes a person's email and names (REQ-000010), only those given, through catalyst-users 0.8.0
  (`update_user`, id-DIR-000007), with `user.update`; a changed email is no longer verified, and the username never
  changes. It still enables or disables a person, in the same call if asked, each change authorized on its own.
- catalyst-users 0.8.0.

## 0.3.0 — 2026-10-30

- The criterion schema v1 (REQ-000009): `rules` may hold `naming` (`max_length` an integer) and `branches` (a list of
  strings), and nothing else; later versions extend it. The rules stay kept as JSON, not mapped to catalyst rules.
- Criteria kept under the stub stay valid as v0: repeating such a create with the same rules changes nothing; new
  criteria and changed rules are checked by v1.

## 0.2.0 — 2026-10-30

The first working release. Every tool works over stdio; over streamable HTTP the server only reads.

- Watches (REQ-000004): a commit made by hand and not yet pushed is adopted into the criterion's journal as the
  identity that started the watch; a saved file refreshes the working-tree diff and writes nothing.
- Criteria (REQ-000003): `create_criterion` and `create_criterion_from_repo`, validated first, their local steps
  undone on failure, the Keycloak group last and safe to repeat.
- Templates (REQ-000005): a checked, versioned catalog that fills in `create_criterion`'s arguments; ships `blank`
  1.0.0.
- People (REQ-000001, REQ-000006): register, enable or disable, grant realm roles, list and read, through
  catalyst-users.
- Authorization (REQ-000002, REQ-000006, REQ-000007): every write through one gate, every decision logged; the realm
  role `catalyst-admin` creates criteria and manages people, a criterion's admins update and watch it; read-only over
  HTTP, where catalyst would sign as the server.
- One setting finds catalyst (REQ-000008): `CATALYST_MCP_CATALYST_ROOT`; a new criterion signs through
  catalyst-users' issuer.
- Fixed BUG-000001: a repeated create reported the group as made when catalyst-users only refreshed it.

## 0.1.0 — 2026-10-28

The skeleton: settings, the server over stdio or streamable HTTP, and every tool declared.
