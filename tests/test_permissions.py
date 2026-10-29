from dataclasses import replace
from typing import Any

import pytest
from catalyst_users.provider import Whoami

from catalyst_mcp.auth import Action, Actor, RoleMapping
from catalyst_mcp.auth.permissions import CRITERION_ACTIONS, REALM_ACTIONS

ALICE = Actor.from_identity(
    Whoami(issuer="https://id/realms/c", subject="sub-alice", username="alice", name="A"), "session"
)
ADMIN = replace(ALICE, roles=("catalyst-admin", "developer"))


class Directory:
    def __init__(self, criteria: list[dict[str, Any]] | None = None, fail: Exception | None = None) -> None:
        self.listed = criteria if criteria is not None else [{"name": "c1", "admins": ["alice"]}]
        self.fail = fail
        self.asked = 0

    async def criteria(self) -> list[dict[str, Any]]:
        self.asked += 1
        if self.fail:
            raise self.fail
        return self.listed


@pytest.mark.parametrize("action", sorted(REALM_ACTIONS))
async def test_a_realm_action_needs_the_catalyst_admin_role(action: Action) -> None:
    mapping = RoleMapping(Directory())  # type: ignore[arg-type]
    allowed = await mapping.can(ADMIN, action, "c1" if action == Action.CRITERION_CREATE else None)
    assert allowed.allowed and "catalyst-admin" in allowed.reason
    refused = await mapping.can(ALICE, action, None)
    assert (
        not refused.allowed
        and refused.reason == f"{action} needs the realm role catalyst-admin, which alice does not have"
    )


@pytest.mark.parametrize("action", sorted(CRITERION_ACTIONS))
async def test_a_criterion_action_needs_an_admin_of_that_criterion_asked_now(action: Action) -> None:
    directory = Directory([{"name": "c1", "admins": ["alice"]}, {"name": "c2", "admins": ["bob"]}])
    mapping = RoleMapping(directory)  # type: ignore[arg-type]
    assert (await mapping.can(ALICE, action, "c1")).allowed
    other = await mapping.can(ADMIN, action, "c2")  # the realm role is no criterion's admin right
    assert not other.allowed and "needs an admin of criterion c2, which alice is not" in other.reason
    directory.listed.append({"name": "c3", "admins": ["alice"]})  # just created: no new login needed
    assert (await mapping.can(ALICE, action, "c3")).allowed
    assert directory.asked == 3


@pytest.mark.parametrize(
    ("directory", "criterion", "reason"),
    [
        (Directory([]), "c1", "criterion c1 has no group in the identity provider"),
        (Directory([{"name": "c1", "admins": None}]), "c1", "are not visible to this session"),
        (Directory(fail=RuntimeError("token=secret")), "c1", "could not be asked (RuntimeError)"),
        (Directory(), None, "needs a criterion"),
    ],
)
async def test_what_cannot_be_answered_is_refused(directory: Directory, criterion: str | None, reason: str) -> None:
    decision = await RoleMapping(directory).can(ALICE, Action.WATCH_START, criterion)  # type: ignore[arg-type]
    assert not decision.allowed and reason in decision.reason and "secret" not in decision.reason


async def test_reading_people_is_no_ones_right_here() -> None:
    decision = await RoleMapping(Directory()).can(ADMIN, Action.USER_READ, None)  # type: ignore[arg-type]
    assert not decision.allowed and decision.reason == "no one may user.read"


def test_every_action_is_mapped_or_refused_on_purpose() -> None:
    assert REALM_ACTIONS | CRITERION_ACTIONS | {Action.USER_READ} == set(Action)
    assert not REALM_ACTIONS & CRITERION_ACTIONS
