"""У каждого поля в админке есть пояснение для значка «i», и значок рисуется на страницах."""

import pytest
from django.contrib import admin
from django.urls import reverse

from core.demo import load_demo
from core.field_help import field_help
from production.models import ProductModel

pytestmark = pytest.mark.django_db

OUR_APPS = {"core", "catalog", "production", "sales", "gallery", "stock"}
SKIP = {"id", "name_key", "created_at", "updated_at", "content_type", "object_id"}


def admin_models():
    """Модели с формами (у колонок таблиц-инлайнов свои подсказки unfold, значка «i» там нет)."""
    return {
        model: model_admin
        for model, model_admin in admin.site._registry.items()
        if model._meta.app_label in OUR_APPS
    }


def test_every_field_has_meaning():
    missing = []
    for model, model_admin in admin_models().items():
        for field in model._meta.get_fields():
            if not getattr(field, "concrete", False) or field.auto_created or not field.editable:
                continue
            if field.name in SKIP or field.verbose_name == field.name:  # служебные ссылки
                continue
            if not field_help(model, field.name):
                missing.append(f"{model._meta.label_lower}.{field.name}")
        for name in getattr(model_admin, "readonly_fields", ()):
            if callable(getattr(model_admin, name, None)) and not field_help(model, name):
                missing.append(f"{model._meta.label_lower}.{name} (вычисляемое)")
    assert not missing, "Нет пояснения для: " + ", ".join(sorted(missing))


def test_meaning_icon_rendered(client, admin_user):
    load_demo()
    client.force_login(admin_user)
    luna = ProductModel.objects.get(name="Светильник Луна")
    html = client.get(
        reverse("admin:production_productmodel_change", args=[luna.pk])
    ).content.decode()
    assert html.count('title="Что это за поле"') > 10
    assert "база для описаний на площадках" in html  # пояснение в значке, не под полем
    assert "База для описаний на площадках." not in html  # из help_text убрано
    assert "Габариты готового изделия без упаковки" in html  # важное — под полем, как и было
    assert "x-teleport" in html
    material_add = client.get(reverse("admin:catalog_material_add")).content.decode()
    assert "Название линейки без цвета" in material_add
