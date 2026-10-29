"""The criterion schema (mcp-CRIT-000001). The real one is a pending input: until it is given, a stub, and nothing
outside this module assumes its shape.

A criterion document is `{"id", "name", "rules"}`; `validate` returns every error found, empty when it is valid.
"""

from __future__ import annotations

from typing import Any, Protocol

from jsonschema import Draft202012Validator

CRITERION_NAME = r"^[a-z0-9][a-z0-9._-]{0,62}$"  # catalyst-users' criterion name, catalyst's project_name

STUB: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/cantica-tech/catalyst-mcp/schemas/criterion-stub-0.json",
    "title": "Criterion (stub)",
    "type": "object",
    "required": ["id", "name", "rules"],
    "properties": {
        "id": {"type": "string", "pattern": CRITERION_NAME},
        "name": {"type": "string", "minLength": 1},
        "rules": {"type": "object"},
    },
}


class CriterionSchema(Protocol):
    id: str

    def validate(self, document: dict[str, Any]) -> list[str]: ...


class StubSchema:
    id = STUB["$id"]

    def __init__(self) -> None:
        self._validator = Draft202012Validator(STUB)

    def validate(self, document: dict[str, Any]) -> list[str]:
        errors = sorted(self._validator.iter_errors(document), key=lambda e: list(e.absolute_path))
        return [f"{'/'.join(str(p) for p in e.absolute_path) or '(document)'}: {e.message}" for e in errors]


def current_schema() -> CriterionSchema:
    return StubSchema()
