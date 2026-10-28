import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from catalyst_users.provider import Whoami

from catalyst_mcp.auth import Action, Actor, AuthorizationMismatchError, Decision, Gate
from catalyst_mcp.criterion import (
    CatalystCli,
    CatalystCliError,
    CriterionExistsError,
    CriterionPartialError,
    CriterionService,
    InvalidCriterionError,
    StubSchema,
)
from catalyst_mcp.criterion.service import RULES_FILE
from catalyst_mcp.iam import IamError
from catalyst_mcp.settings import Settings

ALICE = Actor.from_identity(
    Whoami(issuer="https://id/realms/c", subject="sub-alice", username="alice", name="A"), "session"
)
RULES = {"naming": {"max_length": 40}}


class Allow:
    async def can(self, actor: Actor, action: Action, criterion_id: str | None) -> Decision:
        return Decision.allow("test")


async def authorized(name: str = "c1", action: Action = Action.CRITERION_CREATE):
    return await Gate(Allow()).authorize(ALICE, action, name)


class FakeCli:
    """catalyst's command line: `init` writes the project file and the criterion, as catalyst does."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.calls: list[tuple[str, Path, tuple[Any, ...]]] = []
        self.fail: dict[str, Exception] = {}

    def _call(self, verb: str, repo: Path, *args: Any) -> None:
        self.calls.append((verb, repo, args))
        if verb in self.fail:
            raise self.fail[verb]

    async def init(self, repo: Path, name: str, module: str, **options: Any) -> str:
        self._call("init", repo, name, module, options)
        (repo / "catalyst.toml").write_text(f'project_name = "{name}"\nmodule = "{module}"\n')
        home = self.settings.criterion_home(name)
        home.mkdir(parents=True)
        (home / "README.md").write_text("criterion")
        return ""

    async def journal_append(self, repo: Path, **entry: Any) -> str:
        self._call("journal", repo, entry["files"])
        return ""

    async def members_add(self, repo: Path, username: str, reason: str) -> str:
        self._call("members", repo, username)
        return ""

    def verbs(self) -> list[str]:
        return [verb for verb, _, _ in self.calls]


class FakeDirectory:
    def __init__(self) -> None:
        self.groups: dict[str, list[str]] = {}
        self.members: dict[str, list[str]] = {}
        self.fail: Exception | None = None

    async def criteria(self) -> list[dict[str, Any]]:
        return [{"name": n, "admins": a} for n, a in self.groups.items()]

    async def add_criterion(self, name: str, admin: str | None = None) -> list[str]:
        if self.fail:
            raise self.fail
        if name in self.groups:
            return []
        self.groups[name] = [admin] if admin else []
        return [f"/criteria/{name}", f"/criteria/{name}/admins", f"/criteria/{name}/members"]

    async def criterion_members(self, name: str) -> list[str]:
        return self.members.get(name, [])


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(catalyst_home=tmp_path / "home", projects_root=tmp_path / "projects")


@pytest.fixture
def cli(settings: Settings) -> FakeCli:
    return FakeCli(settings)


@pytest.fixture
def directory() -> FakeDirectory:
    return FakeDirectory()


@pytest.fixture
def service(cli: FakeCli, directory: FakeDirectory, settings: Settings) -> CriterionService:
    return CriterionService(cli, directory, settings)  # type: ignore[arg-type]


def git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    return path


# --- the schema --------------------------------------------------------------------------------------------------


def test_the_stub_schema() -> None:
    schema = StubSchema()
    assert schema.validate({"id": "c1", "name": "C 1", "rules": {}}) == []
    assert schema.validate({"id": "Bad/Name", "name": "", "rules": []}) == [
        "id: 'Bad/Name' does not match '^[a-z0-9][a-z0-9._-]{0,62}$'",
        "name: '' should be non-empty",
        "rules: [] is not of type 'object'",
    ]
    assert schema.validate({}) == [
        "(document): 'id' is a required property",
        "(document): 'name' is a required property",
        "(document): 'rules' is a required property",
    ]


# --- the catalyst command line -----------------------------------------------------------------------------------


@pytest.fixture
def fake_catalyst(tmp_path: Path) -> list[str]:
    script = tmp_path / "catalyst.py"
    script.write_text(
        "import sys\n"
        "verb = sys.argv[1]\n"
        "if verb == 'ok': print('done', *sys.argv[2:])\n"
        "elif verb == 'assent': print('publish?', file=sys.stderr); sys.exit(3)\n"
        "else: print('boom with Bearer abc.def', file=sys.stderr); sys.exit(1)\n"
    )
    return [sys.executable, str(script)]


async def test_the_command_line_runs_in_the_project(fake_catalyst: list[str], tmp_path: Path) -> None:
    assert await CatalystCli(fake_catalyst).run(tmp_path, "ok", "a b") == "done a b\n"


async def test_a_failing_command_names_itself_and_hides_tokens(fake_catalyst: list[str], tmp_path: Path) -> None:
    with pytest.raises(CatalystCliError, match=r"catalyst fail failed \(exit 1\): boom with Bearer \[redacted\]"):
        await CatalystCli(fake_catalyst).run(tmp_path, "fail")


async def test_asking_for_assent_is_a_failure_never_answered(fake_catalyst: list[str], tmp_path: Path) -> None:
    with pytest.raises(CatalystCliError, match="catalyst assent asks for the user's assent: publish") as err:
        await CatalystCli(fake_catalyst).run(tmp_path, "assent")
    assert err.value.returncode == 3


def test_the_command_line_follows_the_settings(tmp_path: Path) -> None:
    cli = CatalystCli.from_settings(Settings(catalyst_cli=["python3", "c.pyz"], catalyst_home=tmp_path))
    assert cli.argv == ["python3", "c.pyz"]
    assert cli.env is not None
    assert cli.env["CATALYST_HOME"] == str(tmp_path)
    assert CatalystCli.from_settings(Settings()).env is None


# --- creating a criterion ----------------------------------------------------------------------------------------


async def test_create_makes_everything_in_order(
    service: CriterionService, cli: FakeCli, directory: FakeDirectory, settings: Settings
) -> None:
    made = await service.create(await authorized(), "c1", RULES, "alice")
    repo = settings.projects_root / "c1"
    assert made.made == ("repository", "deployment", "rules", "group")
    assert (repo / ".git").is_dir()
    assert cli.verbs() == ["init", "journal"]
    assert cli.calls[0][2][:2] == ("c1", "software-engineering")
    assert cli.calls[1][2] == ([".criterion/catalyst-mcp/criterion.json"],)
    kept = json.loads((settings.criterion_home("c1") / RULES_FILE).read_text())
    assert kept == {"$schema": StubSchema.id, "id": "c1", "name": "c1", "rules": RULES}
    assert directory.groups == {"c1": ["alice"]}


async def test_a_repeated_create_changes_nothing(service: CriterionService, cli: FakeCli) -> None:
    await service.create(await authorized(), "c1", RULES, "alice")
    again = await service.create(await authorized(), "c1", RULES, "alice")
    assert again.unchanged
    assert cli.verbs() == ["init", "journal"]


async def test_create_from_repo_installs_in_it_and_the_actor_owns_it(
    service: CriterionService, directory: FakeDirectory, tmp_path: Path
) -> None:
    repo = git_repo(tmp_path / "work" / "app")
    made = await service.create_from_repo(await authorized("app"), repo, "app")
    assert made.made == ("deployment", "rules", "group")
    assert (repo / "catalyst.toml").is_file()
    assert directory.groups == {"app": ["alice"]}


async def test_nothing_is_made_without_the_right_authorization(service: CriterionService, cli: FakeCli) -> None:
    with pytest.raises(AuthorizationMismatchError):
        await service.create(await authorized("c2"), "c1", RULES, "alice")
    with pytest.raises(AuthorizationMismatchError):
        await service.create(await authorized("c1", Action.CRITERION_UPDATE), "c1", RULES, "alice")
    assert cli.calls == []


async def test_invalid_input_is_refused_before_anything(
    service: CriterionService, cli: FakeCli, settings: Settings, tmp_path: Path
) -> None:
    with pytest.raises(InvalidCriterionError) as err:
        await service.create(await authorized("../x"), "../x", [], " ", ["bob", "bob"])  # type: ignore[arg-type]
    assert err.value.errors == [
        "id: '../x' does not match '^[a-z0-9][a-z0-9._-]{0,62}$'",
        "rules: [] is not of type 'object'",
        "owner: empty",
        "users: a username given twice",
    ]
    with pytest.raises(InvalidCriterionError, match="not the absolute path of a git repository"):
        await service.create_from_repo(await authorized("app"), tmp_path / "nowhere", "app")
    assert cli.calls == []
    assert not settings.projects_root.exists()


async def test_members_are_refused_for_a_criterion_not_served(service: CriterionService, cli: FakeCli) -> None:
    with pytest.raises(InvalidCriterionError, match="not served through catalyst serve, so it has admins only"):
        await service.create(await authorized(), "c1", RULES, "alice", ["bob"])
    assert cli.calls == []


async def test_members_of_a_served_criterion_go_through_catalyst(
    service: CriterionService, cli: FakeCli, directory: FakeDirectory, tmp_path: Path
) -> None:
    repo = git_repo(tmp_path / "served")
    (repo / "catalyst.toml").write_text('project_name = "served"\nshare = "serve"\n')
    directory.groups["served"] = ["alice"]
    directory.members["served"] = ["carol"]
    made = await service.create_from_repo(await authorized("served"), repo, "served", ["bob", "carol"])
    assert made.members_added == ("bob",)
    assert ("members", repo, ("bob",)) in cli.calls


async def test_a_member_that_fails_leaves_a_criterion_to_complete(
    service: CriterionService, cli: FakeCli, directory: FakeDirectory, tmp_path: Path
) -> None:
    repo = git_repo(tmp_path / "served")
    (repo / "catalyst.toml").write_text('project_name = "served"\nshare = "serve"\n')
    cli.fail["members"] = CatalystCliError("members", 1, "the server is down")
    with pytest.raises(CriterionPartialError, match=r"not its members bob: .*repeat the create") as err:
        await service.create_from_repo(await authorized("served"), repo, "served", ["bob"])
    assert err.value.remaining == ("bob",)
    assert directory.groups == {"served": ["alice"]}


@pytest.mark.parametrize("failing", ["init", "journal", "group"])
async def test_a_failure_undoes_every_local_step_this_create_made(
    service: CriterionService, cli: FakeCli, directory: FakeDirectory, settings: Settings, failing: str
) -> None:
    if failing == "group":
        directory.fail = IamError("add_criterion", "the provider could not be reached")
    else:
        cli.fail[failing] = CatalystCliError(failing, 1, "it failed")
    with pytest.raises((CatalystCliError, IamError)):
        await service.create(await authorized(), "c1", RULES, "alice")
    assert not (settings.projects_root / "c1").exists()
    assert not settings.criterion_home("c1").parent.exists()
    assert directory.groups == {}


async def test_a_failure_keeps_what_this_create_did_not_make(
    service: CriterionService, cli: FakeCli, directory: FakeDirectory, settings: Settings, tmp_path: Path
) -> None:
    repo = git_repo(tmp_path / "app")
    (repo / "catalyst.toml").write_text('project_name = "app"\n')
    home = settings.criterion_home("app")
    home.mkdir(parents=True)
    directory.fail = IamError("add_criterion", "unreachable")
    with pytest.raises(IamError):
        await service.create_from_repo(await authorized("app"), repo, "app")
    assert (repo / "catalyst.toml").is_file()
    assert (home / RULES_FILE).is_file()  # journaled in a deployment it did not make: kept, never unrecorded
    directory.fail = None
    assert (await service.create_from_repo(await authorized("app"), repo, "app")).made == ("group",)


async def test_what_exists_as_another_criterion_is_refused(
    service: CriterionService, cli: FakeCli, directory: FakeDirectory, settings: Settings, tmp_path: Path
) -> None:
    directory.groups["c1"] = ["mallory"]
    with pytest.raises(CriterionExistsError, match="exists with another owner"):
        await service.create(await authorized(), "c1", RULES, "alice")

    await service.create(await authorized("c2"), "c2", RULES, "alice")
    with pytest.raises(CriterionExistsError, match="exists with other rules"):
        await service.create(await authorized("c2"), "c2", {"other": {}}, "alice")

    repo = git_repo(tmp_path / "app")
    (repo / "catalyst.toml").write_text('project_name = "elsewhere"\n')
    with pytest.raises(CriterionExistsError, match="governed by the criterion elsewhere"):
        await service.create_from_repo(await authorized("app"), repo, "app")

    settings.criterion_home("taken").mkdir(parents=True)
    with pytest.raises(CriterionExistsError, match="the name taken is taken on this machine"):
        await service.create(await authorized("taken"), "taken", RULES, "alice")

    (settings.projects_root / "squat").mkdir(parents=True)
    with pytest.raises(CriterionExistsError, match="exists and is not the repository of the criterion squat"):
        await service.create(await authorized("squat"), "squat", RULES, "alice")
    assert cli.verbs() == ["init", "journal"]  # only c2 was made
