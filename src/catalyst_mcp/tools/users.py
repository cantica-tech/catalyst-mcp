"""People in the identity provider, through catalyst-users, which acts with this machine's logged-in identity.

Over HTTP these tools are refused (mcp-AUTHZ-000004): catalyst-users would act as the server, not the caller.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from catalyst_mcp.auth import Action
from catalyst_mcp.iam import Person
from catalyst_mcp.services import Services
from catalyst_mcp.tools import READ_ONLY, WRITE, tool_errors

Username = Annotated[str, Field(description="The person's username")]
Detail = Annotated[str | None, Field(description="Left unchanged when omitted")]
CRITERIA = "/criteria/"


def person(p: Person) -> dict[str, Any]:
    return asdict(p)


def register(app: MCPServer, services: Services) -> None:
    async def reading() -> None:
        """People are read with this machine's session: over HTTP that is refused, and logged, by the gate."""
        if services.over_http:
            await services.authorize(Action.USER_READ)

    @app.tool(annotations=WRITE)
    async def register_user(
        username: Username,
        email: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> dict[str, Any]:
        """Register a person. Registering one who exists with the same details changes nothing; with other details
        it is refused."""
        with tool_errors("register_user"):
            await services.authorize(Action.USER_REGISTER)
            registered = await services.directory.register_user(username, email, first_name, last_name)
            return {**person(registered.person), "created": registered.created}

    @app.tool(annotations=WRITE)
    async def update_user(
        username: Username,
        email: Detail = None,
        first_name: Detail = None,
        last_name: Detail = None,
        enabled: Annotated[bool | None, Field(description="Enable or disable; unchanged when omitted")] = None,
    ) -> dict[str, Any]:
        """Change a person's email and names (only those given; a changed email is no longer verified, the username
        never changes), and enable or disable them."""
        with tool_errors("update_user"):
            details = email is not None or first_name is not None or last_name is not None
            if not details and enabled is None:
                await reading()
                return person(await services.directory.get_user(username))
            if details:
                await services.authorize(Action.USER_UPDATE)
                updated = await services.directory.update_user(username, email, first_name, last_name)
                if enabled is None:
                    return person(updated)
            await services.authorize(Action.USER_UPDATE if enabled else Action.USER_DISABLE)
            return person(await services.directory.set_enabled(username, enabled is True))

    @app.tool(annotations=ToolAnnotations(destructive_hint=True, idempotent_hint=True, open_world_hint=False))
    async def delete_user(username: Username) -> dict[str, Any]:
        """Remove a person's access. They are disabled, never deleted: what they signed keeps naming them."""
        with tool_errors("delete_user"):
            await services.authorize(Action.USER_DISABLE)
            return {**person(await services.directory.set_enabled(username, False)), "deleted": False}

    @app.tool(annotations=WRITE)
    async def assign_role(
        username: Username,
        role: Annotated[str, Field(description="The realm role to grant")],
        criterion_id: Annotated[
            str | None, Field(description="Not supported: a criterion's own roles are its admins and members")
        ] = None,
    ) -> dict[str, Any]:
        """Grant a person a realm role. A criterion's own roles are its admins and members, which catalyst keeps
        (`catalyst members add`), so a role within one criterion is refused."""
        with tool_errors("assign_role"):
            if criterion_id is not None:
                raise ToolError(
                    f"assign_role: no role is granted within criterion {criterion_id}: its admins and members are "
                    "catalyst's (catalyst members add)"
                )
            await services.authorize(Action.ROLE_ASSIGN)
            await services.directory.grant_role(username, role)
            return {"username": username, "role": role, "granted": True}

    @app.tool(annotations=READ_ONLY)
    async def list_users(
        criterion_id: Annotated[str | None, Field(description="Only this criterion's members")] = None,
        search: Annotated[str | None, Field(description="Match on username, name or email")] = None,
    ) -> list[dict[str, Any]]:
        """The people in the realm, or in one criterion."""
        with tool_errors("list_users"):
            await reading()
            if criterion_id is None:
                return [person(p) for p in await services.directory.list_users(search)]
            members = await services.directory.criterion_members(criterion_id)
            people = [person(await services.directory.get_user(name)) for name in members]
            needle = (search or "").lower()
            return [p for p in people if not needle or any(needle in str(v).lower() for v in p.values())]

    @app.tool(annotations=READ_ONLY)
    async def get_user(username: Username) -> dict[str, Any]:
        """One person: details, groups, and the criteria they belong to."""
        with tool_errors("get_user"):
            await reading()
            found = person(await services.directory.get_user(username))
            groups = await services.directory.user_groups(username)
            criteria = sorted({g[len(CRITERIA) :].split("/")[0] for g in groups if g.startswith(CRITERIA)})
            return {**found, "groups": groups, "criteria": criteria}
