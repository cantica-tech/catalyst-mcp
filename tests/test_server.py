from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from catalyst_mcp.server import build, parse
from catalyst_mcp.settings import Settings

# each tool with the smallest arguments it accepts
CALLS: dict[str, dict[str, Any]] = {
    "start_watch": {"repo_path": "/repo", "criterion_id": "c1"},
    "stop_watch": {"criterion_id": "c1"},
    "list_watches": {},
    "create_criterion": {"name": "c1", "rules": {}, "owner": "alice"},
    "create_criterion_from_repo": {"repo_path": "/repo", "name": "c1"},
    "list_templates": {},
    "get_template": {"name": "t"},
    "create_from_template": {"name": "t", "params": {}},
    "register_user": {"username": "bob"},
    "update_user": {"username": "bob"},
    "delete_user": {"username": "bob"},
    "assign_role": {"username": "bob", "role": "developer"},
    "list_users": {},
    "get_user": {"username": "bob"},
}
READ_ONLY = {"list_watches", "list_templates", "get_template", "list_users", "get_user"}


@pytest.fixture
def app():
    return build(Settings())


async def test_the_server_exposes_exactly_the_designed_tools(app) -> None:
    tools = {tool.name: tool for tool in await app.list_tools()}
    assert set(tools) == set(CALLS)
    for name, tool in tools.items():
        assert tool.description, name
        assert tool.annotations is not None, name
        assert bool(tool.annotations.read_only_hint) == (name in READ_ONLY), name


async def test_a_watch_mode_is_file_change_or_commit(app) -> None:
    tools = {tool.name: tool for tool in await app.list_tools()}
    mode = tools["start_watch"].input_schema["properties"]["mode"]
    assert mode["enum"] == ["file_change", "commit"]
    assert mode["default"] == "commit"
    with pytest.raises(ToolError):
        await app.call_tool("start_watch", {"repo_path": "/r", "criterion_id": "c1", "mode": "push"})


@pytest.mark.parametrize("name", sorted(CALLS))
async def test_every_tool_says_it_is_not_implemented_yet(app, name: str) -> None:
    with pytest.raises(ToolError, match=f"{name} is not implemented yet"):
        await app.call_tool(name, CALLS[name])


def test_the_command_line_wins_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CATALYST_MCP_TRANSPORT", "stdio")
    monkeypatch.setenv("CATALYST_MCP_HTTP_PORT", "9000")
    settings = parse(["--transport", "streamable-http", "--port", "9100"])
    assert settings.transport == "streamable-http"
    assert settings.http_port == 9100
    assert settings.http_host == "127.0.0.1"
