"""Criterion templates (mcp-TMPL-000001, mcp-TMPL-000002): the catalog, its checks, and criteria made from them."""

from catalyst_mcp.templates.loader import (
    CATALOG,
    FilledCriterion,
    InvalidTemplateError,
    Template,
    TemplateCatalog,
    TemplateNotFoundError,
    create_from_template,
)

__all__ = [
    "CATALOG",
    "FilledCriterion",
    "InvalidTemplateError",
    "Template",
    "TemplateCatalog",
    "TemplateNotFoundError",
    "create_from_template",
]
