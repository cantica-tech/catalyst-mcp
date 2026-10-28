"""Creating a criterion (mcp-CRIT-000001..000004, mcp-ARCH-000002), only with the gate's authorization.

The order is the one that can be undone: validate everything first; then the local steps (the repository, the
catalyst deployment, the kept rules), each undone in reverse when a later step fails; then the criterion's group in
the identity provider, which cannot be removed and is idempotent, so a failed create is completed by repeating it;
then the members, through catalyst, for a served criterion only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from catalyst_mcp import log
from catalyst_mcp.auth import Action, AuthorizedAction
from catalyst_mcp.criterion.catalyst_cli import CatalystCli
from catalyst_mcp.criterion.schema import CriterionSchema, current_schema
from catalyst_mcp.iam import IdentityDirectory
from catalyst_mcp.settings import Settings

POINTER = "catalyst.toml"
RULES_FILE = Path("catalyst-mcp") / "criterion.json"  # in the criterion; the project sees it as .criterion/<path>


@dataclass(frozen=True)
class CriterionCreated:
    criterion_id: str
    repo_path: Path
    criterion_path: Path
    owner: str
    made: tuple[str, ...]  # what this call made: repository, deployment, rules, group
    members_added: tuple[str, ...] = ()

    @property
    def unchanged(self) -> bool:
        return not self.made and not self.members_added


class InvalidCriterionError(Exception):
    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__("invalid criterion: " + "; ".join(self.errors))


class CriterionExistsError(Exception):
    pass


class CriterionPartialError(Exception):
    """The criterion and its group are made, some members are not: repeating the create adds them."""

    def __init__(self, criterion_id: str, made: Sequence[str], remaining: Sequence[str], cause: Exception) -> None:
        self.criterion_id = criterion_id
        self.made = tuple(made)
        self.remaining = tuple(remaining)
        super().__init__(
            f"criterion {criterion_id} made ({', '.join(made) or 'nothing new'}), but not its members "
            f"{', '.join(remaining)}: {cause}; repeat the create to add them"
        )


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}


class CriterionService:
    def __init__(
        self,
        cli: CatalystCli,
        directory: IdentityDirectory,
        settings: Settings,
        schema: CriterionSchema | None = None,
    ) -> None:
        self.cli = cli
        self.directory = directory
        self.settings = settings
        self.schema = schema or current_schema()
        self.logger = log.get("criterion")

    async def create(
        self,
        authorized: AuthorizedAction,
        name: str,
        rules: dict[str, Any],
        owner: str,
        users: Sequence[str] = (),
    ) -> CriterionCreated:
        """A criterion without a repository: a new one at `<projects root>/<name>` (mcp-CRIT-000003)."""
        return await self._create(authorized, self.settings.projects_root / name, name, rules, owner, users, True)

    async def create_from_repo(
        self,
        authorized: AuthorizedAction,
        repo_path: str | Path,
        name: str,
        users: Sequence[str] = (),
        owner: str | None = None,
    ) -> CriterionCreated:
        """A criterion mirroring an existing local git repository; its owner is the actor unless named."""
        repo = Path(repo_path).expanduser()
        if not repo.is_absolute() or not (repo / ".git").exists():
            authorized.require(Action.CRITERION_CREATE, name)
            raise InvalidCriterionError([f"repo_path: {repo} is not the absolute path of a git repository"])
        return await self._create(authorized, repo, name, {}, owner or authorized.actor.username, users, False)

    async def _create(
        self,
        authorized: AuthorizedAction,
        repo: Path,
        name: str,
        rules: dict[str, Any],
        owner: str,
        users: Sequence[str],
        new_repo: bool,
    ) -> CriterionCreated:
        authorized.require(Action.CRITERION_CREATE, name)
        document = {"id": name, "name": name, "rules": rules}
        self._validate(document, owner, users)
        pointer = _read_toml(repo / POINTER)
        if users and pointer.get("share") != "serve":
            raise InvalidCriterionError(
                [f"users: {name} is not served through catalyst serve, so it has admins only; add members once it is"]
            )
        home = self.settings.criterion_home(name)
        stored = await self._existing(repo, name, owner, document, pointer, home, new_repo)

        made: list[str] = []
        undo: list[tuple[str, Callable[[], None]]] = []
        try:
            if new_repo and not repo.exists():
                await self._git_init(repo)
                made.append("repository")
                undo.append(("repository", lambda: shutil.rmtree(repo)))
            if not pointer:
                parent_existed = home.parent.exists()
                await self.cli.init(
                    repo,
                    name,
                    self.settings.criterion_module,
                    kernel=self.settings.catalyst_kernel,
                    module_dir=self.settings.criterion_module_dir,
                    agent=self.settings.criterion_agent,
                    identity_issuer=self.settings.identity_issuer,
                )
                made.append("deployment")
                undo.append(("deployment", lambda: self._undeploy(repo, home, parent_existed)))
            if stored is None:
                await self._keep_rules(repo, home, document, authorized, undo, own_deployment=not pointer)
                made.append("rules")
            if await self.directory.add_criterion(name, admin=owner):
                made.append("group")
        except Exception as err:
            self._undo(name, undo, err)
            raise

        added = await self._add_members(repo, name, users, authorized, made)
        log.event(
            self.logger,
            "criterion.created" if made or added else "criterion.unchanged",
            criterion=name,
            actor=authorized.actor.describe(),
            made=made,
            members_added=added,
        )
        return CriterionCreated(name, repo, home, owner, tuple(made), tuple(added))

    def _validate(self, document: dict[str, Any], owner: str, users: Sequence[str]) -> None:
        errors = self.schema.validate(document)
        if not owner.strip():
            errors.append("owner: empty")
        if any(not u.strip() for u in users):
            errors.append("users: an empty username")
        if len(set(users)) != len(users):
            errors.append("users: a username given twice")
        if errors:
            raise InvalidCriterionError(errors)

    async def _existing(
        self,
        repo: Path,
        name: str,
        owner: str,
        document: dict[str, Any],
        pointer: dict[str, Any],
        home: Path,
        new_repo: bool,
    ) -> dict[str, Any] | None:
        """What already exists of this criterion (the kept rules, if any); `CriterionExistsError` when what exists
        is not this criterion, or not with this owner and these rules."""
        if pointer and pointer.get("project_name") != name:
            raise CriterionExistsError(f"{repo} is governed by the criterion {pointer.get('project_name')}")
        if new_repo and repo.exists() and not pointer:
            raise CriterionExistsError(f"{repo} exists and is not the repository of the criterion {name}")
        if not pointer and home.exists():
            raise CriterionExistsError(f"the name {name} is taken on this machine ({home})")
        group = next((c for c in await self.directory.criteria() if c.get("name") == name), None)
        if group is not None and owner not in (group.get("admins") or ()):
            raise CriterionExistsError(f"the criterion {name} exists with another owner")
        stored = self._stored(home) if pointer else None
        if stored is not None and stored != document:
            raise CriterionExistsError(f"the criterion {name} exists with other rules")
        return stored

    @staticmethod
    def _stored(home: Path) -> dict[str, Any] | None:
        path = home / RULES_FILE
        if not path.is_file():
            return None
        kept = json.loads(path.read_text(encoding="utf-8"))
        return {k: kept.get(k) for k in ("id", "name", "rules")}

    async def _keep_rules(
        self,
        repo: Path,
        home: Path,
        document: dict[str, Any],
        authorized: AuthorizedAction,
        undo: list[tuple[str, Callable[[], None]]],
        own_deployment: bool,
    ) -> None:
        path = home / RULES_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"$schema": self.schema.id, **document}, indent=2) + "\n", encoding="utf-8")
        undo.append(("rules", lambda: path.unlink(missing_ok=True)))
        await self.cli.journal_append(
            repo,
            action="create",
            artifact=f"criterion {document['id']}",
            intent=f"the criterion's rules as {authorized.actor.username} gave them, validated against {self.schema.id}",
            files=[f".criterion/{RULES_FILE.as_posix()}"],
        )
        if not own_deployment:  # journaled in a deployment this create did not make: removing it would unrecord it
            undo.pop()

    async def _add_members(
        self, repo: Path, name: str, users: Sequence[str], authorized: AuthorizedAction, made: list[str]
    ) -> list[str]:
        if not users:
            return []
        current = set(await self.directory.criterion_members(name))
        added: list[str] = []
        for i, username in enumerate(users):
            if username in current:
                continue
            try:
                await self.cli.members_add(
                    repo, username, reason=f"named a member when {authorized.actor.username} created the criterion"
                )
            except Exception as err:
                raise CriterionPartialError(name, made, [u for u in users[i:] if u not in current], err) from err
            added.append(username)
        return added

    async def _git_init(self, repo: Path) -> None:
        repo.mkdir(parents=True)
        proc = await asyncio.create_subprocess_exec(
            "git",
            "init",
            "-q",
            "-b",
            "main",
            cwd=repo,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            shutil.rmtree(repo, ignore_errors=True)
            raise RuntimeError(f"git init failed: {err.decode(errors='replace').strip()}")

    @staticmethod
    def _undeploy(repo: Path, home: Path, parent_existed: bool) -> None:
        """Undo `catalyst init` of this create: its project file, its criterion and the journal's ref."""
        (repo / POINTER).unlink(missing_ok=True)
        shutil.rmtree(home.parent if not parent_existed else home, ignore_errors=True)
        ref = repo / ".git" / "refs" / "catalyst" / "journal"
        ref.unlink(missing_ok=True)

    def _undo(self, name: str, undo: list[tuple[str, Callable[[], None]]], cause: Exception) -> None:
        undone, failed = [], []
        for step, action in reversed(undo):
            try:
                action()
                undone.append(step)
            except Exception as err:
                failed.append(f"{step} ({type(err).__name__})")
        log.event(
            self.logger,
            "criterion.undone",
            level=logging.ERROR if failed else logging.WARNING,
            criterion=name,
            cause=str(cause),
            undone=undone,
            failed=failed,
        )
