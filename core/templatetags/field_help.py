"""Теги для значка «i» с пояснением поля (см. core/field_help.py)."""

from django import template

from core.field_help import field_help

register = template.Library()


@register.simple_tag
def field_meaning(model, field) -> str:
    """Пояснение для поля формы: AdminField (field.field.name) или AdminReadonlyField (dict)."""
    inner = getattr(field, "field", None)
    name = inner.get("name") if isinstance(inner, dict) else getattr(inner, "name", None)
    return field_help(model, name)
