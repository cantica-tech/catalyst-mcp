import json
from pathlib import Path
from typing import Any

import pytest
from catalyst_users.provider import Whoami
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from catalyst_mcp import log
from catalyst_mcp.auth import Action, Actor, Decision, DenyAll, StdioOnly
from catalyst_mcp.auth.token import CatalystUsersTokenVerifier
from catalyst_mcp.criterion import CatalystCliError
from catalyst_mcp.iam import IamConflictError, Person, Registered
from catalyst_mcp.server import build, parse
from catalyst_mcp.services import Services
from catalyst_mcp.settings import Settings
from catalyst_mcp.tools import criteria, templates, users, watch
from test_criterion import FakeCli
from test_watch import git

ALICE = Actor.from_identity(
    Whoami(issuer="https://id/realms/c", subject="sub-alice", username="alice", name="A"), "session"
)

# each tool with the smallest arguments it accepts
CALLS: dict[str, dict[str, Any]] = {
    "start_watch": {"repo_path": "/repo", "criterion_id": "c1"},
    "stop_watch": {"criterion_id": "c1"},
    "list_watches": {},
    "create_criterion": {"name": "c1", "rules": {}, "owner": "alice"},
    "create_criterion_from_repo": {"repo_path": "/repo", "name": "c1"},
    "list_templates": {},
    "get_template": {"name": "blank"},
    "create_from_template": {"name": "blank", "params": {"name": "c1"}},
    "register_user": {"username": "bob"},
    "update_user": {"username": "bob", "enabled": False},
    "delete_user": {"username": "bob"},
    "assign_role": {"username": "bob", "role": "developer"},
    "list_users": {},
    "get_user": {"username": "bob"},
}
READ_ONLY = {"list_watches", "list_templates", "get_template", "list_users", "get_user"}
# the action each writing tool asks the gate for, and on which criterion
WRITES: dict[str, tuple[Action, str | None]] = {
    "start_watch": (Action.WATCH_START, "c1"),
    "stop_watch": (Action.WATCH_STOP, "c1"),
    "create_criterion": (Action.CRITERION_CREATE, "c1"),
    "create_criterion_from_repo": (Action.CRITERION_CREATE, "c1"),
    "create_from_template": (Action.CRITERION_CREATE, "c1"),
    "register_user": (Action.USER_REGISTER, None),
    "update_user": (Action.USER_DISABLE, None),
    "delete_user": (Action.USER_DISABLE, None),
    "assign_role": (Action.ROLE_ASSIGN, None),
}


class Recording:
    """Permissions that allow, or refuse, and remember what they were asked."""

    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.asked: list[tuple[Action, str | None]] = []

    async def can(self, actor: Actor, action: Action, criterion_id: str | None) -> Decision:
        self.asked.append((action, criterion_id))
        return Decision.allow("test") if self.allow else Decision.deny("test refuses")


class Fixed:
    async def resolve(self) -> Actor:
        return ALICE


class Directory:
    """The identity directory, in memory; it records each call."""

    def __init__(self) -> None:
        self.people = {"bob": Person("bob", "bob@example.org", "Bob B", True)}
        self.groups: dict[str, list[str]] = {}
        self.calls: list[str] = []
        self.fail: Exception | None = None

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if self.fail:
            raise self.fail

    async def list_users(self, search: str | None = None, limit: int = 100) -> list[Person]:
        self._call("list_users")
        return [p for p in self.people.values() if not search or search in p.username]

    async def get_user(self, username: str) -> Person:
        self._call("get_user")
        return self.people[username]

    async def user_groups(self, username: str) -> list[str]:
        self._call("user_groups")
        return ["/criteria/c1/members", "/criteria/c2/admins", "/staff"]

    async def register_user(self, username: str, *details: Any) -> Registered:
        self._call("register_user")
        created = username not in self.people
        self.people.setdefault(username, Person(username, None, username, True))
        return Registered(self.people[username], created)

    async def set_enabled(self, username: str, enabled: bool) -> Person:
        self._call("set_enabled")
        p = self.people[username]
        self.people[username] = Person(p.username, p.email, p.name, enabled)
        return self.people[username]

    async def grant_role(self, username: str, role: str) -> None:
        self._call("grant_role")

    async def criteria(self) -> list[dict[str, Any]]:
        return [{"name": n, "admins": a} for n, a in self.groups.items()]

    async def add_criterion(self, name: str, admin: str | None = None) -> list[str]:
        self._call("add_criterion")
        if name in self.groups:
            return []
        self.groups[name] = [admin] if admin else []
        return [f"/criteria/{name}"]

    async def criterion_members(self, name: str) -> list[str]:
        self._call("criterion_members")
        return ["bob"]


def services(tmp_path: Path, permissions: Any = None, transport: str = "stdio") -> Services:
    settings = Settings(
        transport=transport,  # type: ignore[arg-type]
        projects_root=tmp_path / "projects",
        catalyst_home=tmp_path / "home",
    )
    return Services.from_settings(
        settings,
        directory=Directory(),  # type: ignore[arg-type]
        permissions=permissions or Recording(),
        resolver=Fixed(),
        cli=FakeCli(settings),  # type: ignore[arg-type]
    )


def served(shared: Services) -> MCPServer:
    """The tools over `shared`, whatever its transport (build needs the provider's settings for HTTP)."""
    app = MCPServer("test")
    for module in (watch, criteria, templates, users):
        module.register(app, shared)
    return app


def asked(shared: Services) -> list[tuple[Action, str | None]]:
    return shared.gate.permissions.inner.asked  # type: ignore[attr-defined]


async def call(app: MCPServer, name: str, arguments: dict[str, Any]) -> Any:
    result = await app.call_tool(name, arguments)
    structured = getattr(result, "structured_content", None)
    assert structured is not None, result
    return structured["result"] if set(structured) == {"result"} else structured


@pytest.fixture
def app(tmp_path: Path) -> MCPServer:
    return build(Settings(), services(tmp_path))


@pytest.fixture
def records(capsys: pytest.CaptureFixture[str]):
    log.configure("INFO", "json")

    def read(event: str) -> list[dict[str, Any]]:
        lines = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.startswith("{")]
        return [r for r in lines if r.get("event") == event]

    return read


# --- the surface (mcp-TOOLS-000002) ----------------------------------------------------------------------------


async def test_the_server_exposes_exactly_the_designed_tools(app: MCPServer) -> None:
    tools = {tool.name: tool for tool in await app.list_tools()}
    assert set(tools) == set(CALLS)
    for name, tool in tools.items():
        assert tool.description, name
        assert tool.annotations is not None, name
        assert bool(tool.annotations.read_only_hint) == (name in READ_ONLY), name
    assert tools["delete_user"].annotations and tools["delete_user"].annotations.destructive_hint


async def test_a_watch_mode_is_file_change_or_commit(app: MCPServer) -> None:
    tools = {tool.name: tool for tool in await app.list_tools()}
    mode = tools["start_watch"].input_schema["properties"]["mode"]
    assert mode["enum"] == ["file_change", "commit"]
    assert mode["default"] == "commit"
    with pytest.raises(ToolError):
        await app.call_tool("start_watch", {"repo_path": "/r", "criterion_id": "c1", "mode": "push"})


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_no_tool_is_pending(app: MCPServer, name: str) -> None:
    try:
        await app.call_tool(name, CALLS[name])
    except ToolError as err:
        assert "not implemented" not in str(err)


# --- the gate first (mcp-TOOLS-000003, mcp-AUTHZ-000001) -------------------------------------------------------


@pytest.mark.parametrize("name", sorted(WRITES))
async def test_a_refused_write_calls_nothing(tmp_path: Path, name: str) -> None:
    shared = services(tmp_path, Recording(allow=False))
    with pytest.raises(ToolError, match=r"refused: .* test refuses"):
        await build(shared.settings, shared).call_tool(name, CALLS[name])
    assert asked(shared) == [WRITES[name]]
    assert shared.directory.calls == []  # type: ignore[attr-defined]
    assert shared.cli.calls == []  # type: ignore[attr-defined]


async def test_by_default_every_write_is_refused(tmp_path: Path) -> None:
    shared = services(tmp_path, DenyAll())
    app = build(shared.settings, shared)
    for name in WRITES:
        with pytest.raises(ToolError, match=DenyAll.REASON):
            await app.call_tool(name, CALLS[name])


async def test_an_allowed_create_reaches_the_service(tmp_path: Path) -> None:
    shared = services(tmp_path)
    app = build(shared.settings, shared)
    made = await call(app, "create_criterion", {"name": "c1", "rules": {"a": 1}, "owner": "alice"})
    assert made["made"] == ["repository", "deployment", "rules", "group"] and not made["unchanged"]
    templated = await call(app, "create_from_template", {"name": "blank", "params": {"name": "c2"}})
    assert templated["template"] == "blank@1.0.0" and templated["owner"] == "alice"
    with pytest.raises(ToolError, match="create_criterion: "):
        await call(app, "create_criterion", {"name": "c1", "rules": {"a": 2}, "owner": "alice"})
    assert asked(shared) == [
        (Action.CRITERION_CREATE, "c1"),
        (Action.CRITERION_CREATE, "c2"),
        (Action.CRITERION_CREATE, "c1"),
    ]


async def test_the_people_tools_reach_the_directory(tmp_path: Path) -> None:
    shared = services(tmp_path)
    app = build(shared.settings, shared)
    assert (await call(app, "register_user", {"username": "carol"}))["created"]
    disabled = await call(app, "delete_user", {"username": "bob"})
    assert disabled["enabled"] is False and disabled["deleted"] is False
    assert (await call(app, "update_user", {"username": "bob", "enabled": True}))["enabled"]
    assert (await call(app, "update_user", {"username": "bob"}))["enabled"]  # nothing to change: a read
    assert (await call(app, "assign_role", {"username": "bob", "role": "dev"}))["granted"]
    assert [p["username"] for p in await call(app, "list_users", {})] == ["bob", "carol"]
    assert [p["username"] for p in await call(app, "list_users", {"criterion_id": "c1"})] == ["bob"]
    found = await call(app, "get_user", {"username": "bob"})
    assert found["criteria"] == ["c1", "c2"] and "/staff" in found["groups"]
    assert asked(shared) == [
        (Action.USER_REGISTER, None),
        (Action.USER_DISABLE, None),
        (Action.USER_UPDATE, None),
        (Action.ROLE_ASSIGN, None),
    ]


async def test_what_catalyst_users_cannot_do_is_refused_before_the_gate(tmp_path: Path) -> None:
    shared = services(tmp_path)
    app = build(shared.settings, shared)
    with pytest.raises(ToolError, match="cannot change a person's email, last_name"):
        await app.call_tool("update_user", {"username": "bob", "email": "x@y", "last_name": "Z"})
    with pytest.raises(ToolError, match="no role is granted within criterion c1"):
        await app.call_tool("assign_role", {"username": "bob", "role": "dev", "criterion_id": "c1"})
    assert asked(shared) == []


async def test_a_failure_is_a_tool_error_naming_it_without_a_secret(tmp_path: Path, records) -> None:
    shared = services(tmp_path)
    app = build(shared.settings, shared)
    shared.directory.fail = IamConflictError("register_user", "Bearer abc.def exists", 409)  # type: ignore[attr-defined]
    with pytest.raises(ToolError, match="register_user") as caught:
        await app.call_tool("register_user", {"username": "bob"})
    assert "abc.def" not in str(caught.value)
    shared.directory.fail = None  # type: ignore[attr-defined]
    shared.cli.fail["init"] = CatalystCliError("init", 2, "broken")  # type: ignore[attr-defined]
    with pytest.raises(ToolError, match="catalyst init failed"):
        await app.call_tool("create_criterion", {"name": "c3", "rules": {}, "owner": "alice"})
    shared.directory.fail = RuntimeError("password=hunter2")  # type: ignore[attr-defined]
    with pytest.raises(ToolError, match=r"failed unexpectedly \(RuntimeError\)") as caught:
        await app.call_tool("get_user", {"username": "bob"})
    assert "hunter2" not in str(caught.value)
    assert [r["error"] for r in records("tool.failed")] == ["RuntimeError"]


async def test_templates_and_watches_are_read(app: MCPServer) -> None:
    assert [t["name"] for t in await call(app, "list_templates", {})] == ["blank"]
    assert (await call(app, "get_template", {"name": "blank"}))["version"] == "1.0.0"
    with pytest.raises(ToolError, match="template nope not found"):
        await app.call_tool("get_template", {"name": "nope"})
    assert await call(app, "list_watches", {}) == []


async def test_a_watch_starts_through_its_tool_and_stops_with_the_server(tmp_path: Path) -> None:
    repo = tmp_path / "proj"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "catalyst.toml").write_text('project_name = "c1"\n')
    shared = services(tmp_path)
    app = build(shared.settings, shared)
    started = await call(app, "start_watch", {"repo_path": str(repo), "criterion_id": "c1"})
    assert started["started"] and started["actor"]["username"] == "alice"
    assert [w["criterion_id"] for w in await call(app, "list_watches", {})] == ["c1"]
    lifespan = app.settings.lifespan
    assert lifespan is not None
    async with lifespan(app):
        pass
    assert await call(app, "list_watches", {}) == []


# --- read-only over HTTP (mcp-AUTHZ-000004) --------------------------------------------------------------------


async def test_over_http_every_write_and_people_read_is_refused_and_logged(tmp_path: Path, records) -> None:
    shared = services(tmp_path, Recording(), "streamable-http")
    app = served(shared)
    for name in [*WRITES, "list_users", "get_user", "update_user"]:
        args = {"username": "bob"} if name == "update_user" else CALLS[name]
        with pytest.raises(ToolError, match="over HTTP catalyst would sign"):
            await app.call_tool(name, args)
    assert asked(shared) == []
    assert shared.directory.calls == [] and shared.cli.calls == []  # type: ignore[attr-defined]
    denied = records("authz.decision")
    assert denied and all(r["decision"] == "deny" for r in denied)
    assert {r["action"] for r in denied} >= {"user.read", "watch.start", "criterion.create"}
    assert [t["name"] for t in await call(app, "list_templates", {})] == ["blank"]
    assert await call(app, "list_watches", {}) == []


async def test_stdio_only_defers_to_the_permissions_over_stdio() -> None:
    inner = Recording()
    assert (await StdioOnly(inner, "stdio").can(ALICE, Action.USER_REGISTER, None)).allowed
    assert not (await StdioOnly(inner, "streamable-http").can(ALICE, Action.USER_REGISTER, None)).allowed
    assert inner.asked == [(Action.USER_REGISTER, None)]


def test_the_http_app_verifies_bearer_tokens(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CATALYST_USERS_HOME", str(tmp_path / "users"))
    settings = Settings(
        transport="streamable-http",
        keycloak_url="http://id.example",
        keycloak_realm="r",
        keycloak_api_client_secret="s3cret",  # type: ignore[arg-type]
    )
    app = build(settings, services(tmp_path, transport="streamable-http"))
    assert isinstance(app._token_verifier, CatalystUsersTokenVerifier)
    auth = app.settings.auth
    assert auth is not None and str(auth.issuer_url).rstrip("/") == "http://id.example/realms/r"


def test_the_command_line_wins_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CATALYST_MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("CATALYST_MCP_HTTP_PORT", "9000")
    settings = parse(["--transport", "streamable-http", "--port", "9100"])
    assert settings.transport == "streamable-http"
    assert settings.http_port == 9100
    assert settings.http_host == "127.0.0.1"
