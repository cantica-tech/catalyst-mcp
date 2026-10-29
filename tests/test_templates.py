import json
from pathlib import Path
from typing import Any

import pytest

from catalyst_mcp.auth import Action, AuthorizationMismatchError
from catalyst_mcp.criterion import CriterionService
from catalyst_mcp.settings import Settings
from catalyst_mcp.templates import (
    CATALOG,
    InvalidTemplateError,
    TemplateCatalog,
    TemplateNotFoundError,
    create_from_template,
)
from catalyst_mcp.templates.loader import version_key
from test_criterion import FakeCli, FakeDirectory, authorized

PARAMS = {
    "type": "object",
    "required": ["name"],
    "properties": {"name": {"type": "string"}, "max": {"type": "integer", "default": 40}},
}
CRITERION = {"name": "${name}", "rules": {"naming": {"max": "${max}", "label": "max ${max} for ${name}, $${kept}"}}}


def write(root: Path, name: str, version: str, overrides: dict[str, Any] | None = None, **more: Any) -> Path:
    doc = {"name": name, "version": version, "description": f"{name} {version}", "parameters": PARAMS}
    doc["criterion"] = CRITERION
    doc.update(overrides or {}, **more)
    path = root / name / f"{version}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))
    return path


@pytest.fixture
def catalog(tmp_path: Path) -> TemplateCatalog:
    for version in ("1.0.0", "1.2.0", "1.10.0", "2.0.0-rc.1"):
        write(tmp_path, "svc", version)
    write(tmp_path, "early", "0.1.0-alpha")
    write(tmp_path, "early", "0.1.0-alpha.2")
    return TemplateCatalog(tmp_path)


# --- the catalog and its versions (mcp-TMPL-000001) ------------------------------------------------------------


def test_the_catalog_lists_each_template_with_its_versions_and_latest(catalog: TemplateCatalog) -> None:
    listed = {t["name"]: t for t in catalog.list()}
    assert listed["svc"]["versions"] == ["1.0.0", "1.2.0", "1.10.0", "2.0.0-rc.1"]
    assert listed["svc"]["latest"] == "1.10.0" and listed["svc"]["description"] == "svc 1.10.0"
    assert listed["early"]["latest"] == "0.1.0-alpha.2"  # only pre-releases: the highest of them
    assert catalog.get("svc").version == "1.10.0" and catalog.get("svc", "2.0.0-rc.1").version == "2.0.0-rc.1"
    assert "invalid" not in listed["svc"]


def test_versions_follow_semantic_precedence() -> None:
    ordered = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0", "1.0.1"]
    shuffled = [ordered[i] for i in (3, 6, 0, 5, 2, 4, 1)]
    assert sorted(shuffled, key=version_key) == ordered
    with pytest.raises(ValueError, match="not a semantic version"):
        version_key("1.0")


def test_an_unknown_template_or_version_is_not_found(catalog: TemplateCatalog) -> None:
    with pytest.raises(TemplateNotFoundError, match="nope"):
        catalog.get("nope")
    with pytest.raises(TemplateNotFoundError, match=r"svc@9\.9\.9"):
        catalog.get("svc", "9.9.9")
    assert TemplateCatalog(Path("/no/such/dir")).list() == []


@pytest.mark.parametrize(
    ("version", "overrides", "error"),
    [
        ("1.0", {"version": "1.0"}, "not a semantic version"),
        ("1.0.0", {"name": "other"}, "is not its directory's"),
        ("1.0.0", {"version": "1.0.1"}, "is not its file's"),
        ("1.0.0", {"description": ""}, "should be non-empty"),
        ("1.0.0", {"parameters": {"type": "array"}}, "a JSON Schema for an object"),
        ("1.0.0", {"parameters": {"type": "object", "required": 3}}, "not a valid JSON Schema"),
        ("1.0.0", {"criterion": {"name": "x", "rules": {}, "repo": "/tmp"}}, "Additional properties"),
        ("1.0.0", {"criterion": {"name": "x"}}, "'rules' is a required property"),
        ("1.0.0", {"extra": 1}, "Additional properties"),
    ],
)
def test_an_invalid_template_is_listed_with_its_errors_and_never_used(
    tmp_path: Path, version: str, overrides: dict[str, Any], error: str
) -> None:
    write(tmp_path, "bad", version, overrides)
    catalog = TemplateCatalog(tmp_path)
    [listed] = catalog.list()
    assert listed["versions"] == [] and listed["latest"] is None
    assert any(error in e for e in listed["invalid"][0]["errors"]), listed
    with pytest.raises(InvalidTemplateError, match=error):
        catalog.get("bad")
    with pytest.raises(InvalidTemplateError, match=error):
        catalog.render("bad", {"name": "c1"}, version)


def test_a_file_that_is_not_json_is_invalid(tmp_path: Path) -> None:
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "1.0.0.json").write_text("{not json")
    with pytest.raises(InvalidTemplateError, match="not readable JSON"):
        TemplateCatalog(tmp_path).get("broken", "1.0.0")


def test_an_invalid_version_beside_valid_ones_leaves_them_usable(tmp_path: Path) -> None:
    write(tmp_path, "svc", "1.0.0")
    write(tmp_path, "svc", "1.1.0", description="")
    catalog = TemplateCatalog(tmp_path)
    [listed] = catalog.list()
    assert listed["latest"] == "1.0.0" and listed["invalid"][0]["version"] == "1.1.0"


# --- filling in (mcp-TMPL-000002) ------------------------------------------------------------------------------


def test_placeholders_keep_their_type_or_become_text_and_defaults_fill_in(catalog: TemplateCatalog) -> None:
    filled = catalog.render("svc", {"name": "c1"})
    assert filled.name == "c1" and filled.template == "svc@1.10.0"
    assert filled.rules == {"naming": {"max": 40, "label": "max 40 for c1, ${kept}"}}
    assert filled.owner is None and filled.users == ()
    assert catalog.render("svc", {"name": "c1", "max": 12}).rules["naming"]["max"] == 12


def test_invalid_parameters_and_unknown_placeholders_are_refused_with_every_error(tmp_path: Path) -> None:
    write(tmp_path, "svc", "1.0.0", criterion={"name": "${name}", "rules": {"a": "${nope}", "b": "x ${gone}"}})
    catalog = TemplateCatalog(tmp_path)
    with pytest.raises(InvalidTemplateError) as caught:
        catalog.render("svc", {"max": "many"})
    errors = caught.value.errors
    assert any("'name' is a required property" in e for e in errors)
    assert any(e.startswith("params.max:") for e in errors)
    assert any("${nope}" in e for e in errors) and any("${gone}" in e for e in errors)


def test_arguments_of_the_wrong_type_are_refused(tmp_path: Path) -> None:
    params = {"type": "object", "properties": {"n": {"type": "integer"}, "r": {"type": "array"}}}
    criterion = {"name": "${n}", "rules": "${r}", "owner": "${n}", "users": ["${n}"]}
    write(tmp_path, "svc", "1.0.0", parameters=params, criterion=criterion)
    with pytest.raises(InvalidTemplateError) as caught:
        TemplateCatalog(tmp_path).render("svc", {"n": 3, "r": []})
    assert {e.split(":")[0] for e in caught.value.errors} == {
        "criterion.name",
        "criterion.rules",
        "criterion.owner",
        "criterion.users",
    }


def test_the_shipped_blank_template_loads_and_fills_in() -> None:
    catalog = TemplateCatalog()
    assert catalog.directory == CATALOG
    [blank] = [t for t in catalog.list() if t["name"] == "blank"]
    assert blank["latest"] == "1.0.0" and "invalid" not in blank
    filled = catalog.render("blank", {"name": "c1", "users": ["bob"]})
    assert (filled.name, filled.rules, filled.owner, filled.users) == ("c1", {}, None, ("bob",))
    assert all(t.valid for t in catalog.all())


def test_the_settings_choose_the_catalog(tmp_path: Path) -> None:
    write(tmp_path, "svc", "1.0.0")
    catalog = TemplateCatalog(Settings(templates_dir=tmp_path).templates_dir)
    assert [t["name"] for t in catalog.list()] == ["svc"]


# --- creating from a template ----------------------------------------------------------------------------------


async def test_a_filled_template_is_created_as_create_criterion_creates(
    catalog: TemplateCatalog, tmp_path: Path
) -> None:
    settings = Settings(projects_root=tmp_path / "projects", catalyst_home=tmp_path / "home")
    directory = FakeDirectory()
    service = CriterionService(FakeCli(settings), directory, settings)  # type: ignore[arg-type]
    filled = catalog.render("svc", {"name": "c1"})
    with pytest.raises(AuthorizationMismatchError):
        await create_from_template(service, await authorized("c2"), filled)
    with pytest.raises(AuthorizationMismatchError):
        await create_from_template(service, await authorized("c1", Action.CRITERION_UPDATE), filled)
    made = await create_from_template(service, await authorized("c1"), filled)
    assert made.owner == "alice" and made.made == ("repository", "deployment", "rules", "group")
    assert directory.groups["c1"] == ["alice"]
    again = await create_from_template(service, await authorized("c1"), catalog.render("svc", {"name": "c1"}))
    assert again.unchanged
