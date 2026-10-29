"""Criterion templates (mcp-TMPL-000001, mcp-TMPL-000002): a catalog of versioned JSON files, checked before use,
that fill in `create_criterion`'s arguments.

A template knows nothing of a criterion's shape: its `rules` stay opaque here, and the criterion schema checks them
when the criterion is created, as for `create_criterion`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from catalyst_mcp import log
from catalyst_mcp.auth import AuthorizedAction
from catalyst_mcp.criterion import CriterionCreated, CriterionService

CATALOG = Path(__file__).parent / "catalog"  # the templates catalyst-mcp ships, inside the package

SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
PLACEHOLDER = re.compile(r"\$\$\{|\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
FILLS = {"name", "rules", "owner", "users"}  # create_criterion's arguments

ENVELOPE: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["name", "version", "description", "parameters", "criterion"],
    "additionalProperties": False,
    "properties": {
        "$schema": {"type": "string"},
        "name": {"type": "string", "pattern": r"^[a-z0-9][a-z0-9._-]{0,62}$"},
        "version": {"type": "string"},
        "description": {"type": "string", "minLength": 1},
        "parameters": {"type": "object"},
        "criterion": {
            "type": "object",
            "required": ["name", "rules"],
            "additionalProperties": False,
            "properties": {key: {} for key in sorted(FILLS)},
        },
    },
}


class TemplateNotFoundError(Exception):
    pass


class InvalidTemplateError(Exception):
    def __init__(self, ref: str, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__(f"template {ref}: " + "; ".join(self.errors))


@dataclass(frozen=True)
class Template:
    name: str
    version: str
    path: Path
    document: dict[str, Any] = field(default_factory=dict, repr=False)
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"

    def describe(self) -> dict[str, Any]:
        """What `get_template` shows: the template, or its errors."""
        if not self.valid:
            return {"name": self.name, "version": self.version, "errors": list(self.errors)}
        doc = self.document
        return {
            "name": self.name,
            "version": self.version,
            "description": doc["description"],
            "parameters": doc["parameters"],
            "criterion": doc["criterion"],
        }


@dataclass(frozen=True)
class FilledCriterion:
    """`create_criterion`'s arguments, filled in from a template."""

    name: str
    rules: dict[str, Any]
    owner: str | None
    users: tuple[str, ...]
    template: str  # name@version


def version_key(version: str) -> tuple[Any, ...]:
    """Semantic-version precedence: a release above its pre-releases, numeric identifiers below others."""
    match = SEMVER.match(version)
    if not match:
        raise ValueError(f"not a semantic version: {version}")
    core = tuple(int(part) for part in match.groups()[:3])
    pre = match.group(4)
    if pre is None:
        return (*core, 1, ())
    ids = tuple((0, int(i), "") if i.isdigit() else (1, 0, i) for i in pre.split("."))
    return (*core, 0, ids)


def _prerelease(version: str) -> bool:
    match = SEMVER.match(version)
    return bool(match and match.group(4))


class TemplateCatalog:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or CATALOG
        self.logger = log.get("templates")

    def all(self) -> list[Template]:
        """Every template version in the catalog, valid or with its errors, by name then version."""
        if not self.directory.is_dir():
            return []
        found = [self._load(path) for path in sorted(self.directory.glob("*/*.json"))]
        return sorted(found, key=lambda t: (t.name, _sortable(t.version)))

    def list(self) -> list[dict[str, Any]]:
        """What `list_templates` shows: each template's versions, its latest valid one, and any invalid ones."""
        names: dict[str, list[Template]] = {}
        for template in self.all():
            names.setdefault(template.name, []).append(template)
        listed = []
        for name, versions in names.items():
            valid = [t for t in versions if t.valid]
            latest = _latest(valid)
            entry: dict[str, Any] = {
                "name": name,
                "versions": [t.version for t in valid],
                "latest": latest.version if latest else None,
                "description": latest.document["description"] if latest else None,
            }
            invalid = [{"version": t.version, "errors": list(t.errors)} for t in versions if not t.valid]
            if invalid:
                entry["invalid"] = invalid
            listed.append(entry)
        return listed

    def get(self, name: str, version: str | None = None) -> Template:
        """One valid version of `name`, the latest when `version` is None."""
        versions = [t for t in self.all() if t.name == name]
        if version is not None:
            chosen = next((t for t in versions if t.version == version), None)
            if chosen is None:
                raise TemplateNotFoundError(f"template {name}@{version} not found")
        else:
            chosen = _latest([t for t in versions if t.valid])
            if chosen is None:
                invalid = [t for t in versions if not t.valid]
                if invalid:
                    raise InvalidTemplateError(
                        name, [e for t in invalid for e in (f"{t.version}: {x}" for x in t.errors)]
                    )
                raise TemplateNotFoundError(f"template {name} not found")
        if not chosen.valid:
            raise InvalidTemplateError(chosen.ref, chosen.errors)
        return chosen

    def render(self, name: str, params: Mapping[str, Any], version: str | None = None) -> FilledCriterion:
        """`create_criterion`'s arguments from a template and its parameters (mcp-TMPL-000002)."""
        template = self.get(name, version)
        values = _with_defaults(template.document["parameters"], params)
        validator = Draft202012Validator(template.document["parameters"])
        errors = [_error("params", e) for e in sorted(validator.iter_errors(values), key=lambda e: list(e.path))]
        filled, missing = _fill(template.document["criterion"], values)
        errors += [f"criterion: the placeholder ${{{p}}} has no parameter" for p in sorted(missing)]
        if not errors:
            errors += _argument_errors(filled)
        if errors:
            raise InvalidTemplateError(template.ref, errors)
        return FilledCriterion(
            name=filled["name"],
            rules=filled["rules"],
            owner=filled.get("owner"),
            users=tuple(filled.get("users") or ()),
            template=template.ref,
        )

    def _load(self, path: Path) -> Template:
        name, version = path.parent.name, path.stem
        errors: list[str] = []
        document: Any = {}
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as err:
            errors.append(f"not readable JSON: {err}")
        if not errors:
            errors += [_error("", e) for e in Draft202012Validator(ENVELOPE).iter_errors(document)]
        if not SEMVER.match(version):
            errors.append(f"{version} is not a semantic version (MAJOR.MINOR.PATCH)")
        if isinstance(document, dict):
            if document.get("name") not in (None, name):
                errors.append(f"name: {document.get('name')!r} is not its directory's, {name!r}")
            if document.get("version") not in (None, version):
                errors.append(f"version: {document.get('version')!r} is not its file's, {version!r}")
            errors += _parameter_errors(document.get("parameters"))
        if errors:
            log.event(
                self.logger, "template.invalid", level=logging.WARNING, template=f"{name}@{version}", errors=errors
            )
        return Template(name, version, path, document if isinstance(document, dict) else {}, tuple(errors))


def _parameter_errors(parameters: Any) -> list[str]:
    if not isinstance(parameters, dict):
        return []
    try:
        Draft202012Validator.check_schema(parameters)
    except SchemaError as err:
        return [f"parameters: not a valid JSON Schema ({err.message})"]
    if parameters.get("type") != "object":
        return ['parameters: must be a JSON Schema for an object ("type": "object")']
    return []


def _with_defaults(parameters: dict[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
    defaults = {
        key: spec["default"]
        for key, spec in (parameters.get("properties") or {}).items()
        if isinstance(spec, dict) and "default" in spec
    }
    return {**defaults, **params}


def _fill(value: Any, params: Mapping[str, Any]) -> tuple[Any, set[str]]:
    """`value` with its placeholders filled in, and the placeholders that have no parameter."""
    missing: set[str] = set()

    def fill(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: fill(inner) for key, inner in item.items()}
        if isinstance(item, list):
            return [fill(inner) for inner in item]
        if not isinstance(item, str):
            return item
        whole = PLACEHOLDER.fullmatch(item)
        if whole and whole.group(1):
            if whole.group(1) not in params:
                missing.add(whole.group(1))
                return item
            return params[whole.group(1)]

        def text(match: re.Match[str]) -> str:
            key = match.group(1)
            if key is None:  # $${
                return "${"
            if key not in params:
                missing.add(key)
                return match.group(0)
            inner = params[key]
            return inner if isinstance(inner, str) else json.dumps(inner)

        return PLACEHOLDER.sub(text, item)

    return fill(value), missing


def _argument_errors(filled: dict[str, Any]) -> list[str]:
    errors = []
    if not isinstance(filled.get("name"), str) or not filled["name"]:
        errors.append("criterion.name: must be a non-empty string")
    if not isinstance(filled.get("rules"), dict):
        errors.append("criterion.rules: must be an object")
    if filled.get("owner") is not None and not isinstance(filled["owner"], str):
        errors.append("criterion.owner: must be a string")
    users = filled.get("users")
    if users is not None and not (isinstance(users, list) and all(isinstance(u, str) for u in users)):
        errors.append("criterion.users: must be a list of usernames")
    return errors


def _error(prefix: str, err: Any) -> str:
    where = ".".join(str(p) for p in err.absolute_path)
    where = f"{prefix}.{where}" if prefix and where else (prefix or where)
    return f"{where}: {err.message}" if where else str(err.message)


def _latest(templates: Sequence[Template]) -> Template | None:
    releases = [t for t in templates if not _prerelease(t.version)]
    pool = releases or list(templates)
    return max(pool, key=lambda t: version_key(t.version), default=None)


def _sortable(version: str) -> tuple[int, Any]:
    try:
        return (0, version_key(version))
    except ValueError:
        return (1, version)


async def create_from_template(
    service: CriterionService, authorized: AuthorizedAction, filled: FilledCriterion
) -> CriterionCreated:
    """Create what a template filled in, as `create_criterion` does (mcp-TMPL-000002); the actor owns it unless the
    template names an owner."""
    owner = filled.owner or authorized.actor.username
    return await service.create(authorized, filled.name, filled.rules, owner, filled.users)
