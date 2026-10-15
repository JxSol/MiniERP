"""У каждого раздела меню есть описание, и кнопка «О разделе» видна на странице списка."""

import pytest
from django.contrib import admin
from django.urls import reverse

from core.help import PAGE_HELP

pytestmark = pytest.mark.django_db

OUR_APPS = {"core", "catalog", "production", "sales", "stock", "auth"}


def registered_labels():
    return {
        f"{m._meta.app_label}.{m._meta.model_name}"
        for m in admin.site._registry
        if m._meta.app_label in OUR_APPS
    }


def test_every_registered_model_has_help():
    missing = registered_labels() - set(PAGE_HELP)
    assert not missing, f"Нет описания для: {sorted(missing)}"
    stale = set(PAGE_HELP) - registered_labels()
    assert not stale, f"Описание без раздела: {sorted(stale)}"


def test_help_button_is_rendered(client, admin_user):
    client.force_login(admin_user)
    for label in sorted(PAGE_HELP):
        app, model = label.split(".")
        html = client.get(reverse(f"admin:{app}_{model}_changelist"), follow=True).content.decode()
        assert "О разделе" in html, label
        assert PAGE_HELP[label]["title"] in html, label
