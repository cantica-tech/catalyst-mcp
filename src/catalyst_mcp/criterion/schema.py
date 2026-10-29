"""The criterion schema (mcp-CRIT-000001), versioned; nothing outside this module assumes its shape.

v1 is the owner's (2026-10-30): `rules` may hold `naming` (an object whose `max_length` is an integer) and `branches`
(a list of strings), and nothing else; a later version extends it. v0 is the stub every criterion was checked against
before: `rules` any object. A criterion kept under v0 stays valid. A criterion document is `{"id", "name", "rules"}`;
`validate` returns every error found, empty when it is valid.
"""

from __future__ import annotations

from typing import Any, Protocol

from jsonschema import Draft202012Validator

CRITERION_NAME = r"^[a-z0-9][a-z0-9._-]{0,62}$"  # catalyst-users' criterion name, catalyst's project_name
BASE = "https://github.com/cantica-tech/catalyst-mcp/schemas"

V0: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": f"{BASE}/criterion-stub-0.json",
    "title": "Criterion (stub)",
    "type": "object",
    "required": ["id", "name", "rules"],
    "properties": {
        "id": {"type": "string", "pattern": CRITERION_NAME},
        "name": {"type": "string", "minLength": 1},
        "rules": {"type": "object"},
    },
}

V1: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": f"{BASE}/criterion-1.json",
    "title": "Criterion",
    "type": "object",
    "required": ["id", "name", "rules"],
    "properties": {
        "id": {"type": "string", "pattern": CRITERION_NAME},
        "name": {"type": "string"},
        "rules": {
            "type": "object",
            "properties": {
                "naming": {"type": "object", "properties": {"max_length": {"type": "integer"}}},
                "branches": {"type": "array", "items": {"type": "string"}},
            },
            "additionalProperties": False,
        },
    },
}


class CriterionSchema(Protocol):
    id: str

    def validate(self, document: dict[str, Any]) -> list[str]: ...


class JsonSchema:
    """One version of the criterion schema."""

    def __init__(self, schema: dict[str, Any]) -> None:
        self.id: str = schema["$id"]
        self._validator = Draft202012Validator(schema)

    def validate(self, document: dict[str, Any]) -> list[str]:
        errors = sorted(self._validator.iter_errors(document), key=lambda e: list(e.absolute_path))
        return [f"{'/'.join(str(p) for p in e.absolute_path) or '(document)'}: {e.message}" for e in errors]


class StubSchema(JsonSchema):
    id = V0["$id"]

    def __init__(self) -> None:
        super().__init__(V0)


SCHEMAS: dict[str, dict[str, Any]] = {V0["$id"]: V0, V1["$id"]: V1}  # every version a kept criterion may name


def current_schema() -> CriterionSchema:
    return JsonSchema(V1)


def schema_named(schema_id: str | None) -> CriterionSchema | None:
    """The version a kept criterion names, if catalyst-mcp knows it."""
    found = SCHEMAS.get(schema_id or "")
    return JsonSchema(found) if found else None
