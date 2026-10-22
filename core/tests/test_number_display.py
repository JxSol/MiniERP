"""
Ни на одной странице админки числа не показываются сырыми: «600,0000», «1000.0000»,
«9000.00». Все количества и деньги идут через помощники core.admin_utils.
"""

import html
import re

import pytest
from django.contrib import admin
from django.urls import reverse

from core.demo import load_demo
from costing.services import recalculate_all

pytestmark = pytest.mark.django_db

OUR_APPS = {"core", "catalog", "production", "sales", "gallery", "stock"}

# «19.10.2026» не считается: у даты точка с обеих сторон хотя бы у одной из частей
DOT_DECIMAL = re.compile(r"(?<![\d.])\d+\.\d+(?![\d.])")
# «600,0000», «2,0000» — так Django печатает DecimalField без форматирования
TRAILING_ZEROS = re.compile(r"\d,\d*0{3}(?!\d)")
# теги с учётом кавычек: в классах Tailwind бывает «>» ([&>.active])
TAGS = re.compile(
    r"<(script|style)\b.*?</\1>|<[^>\"']*(?:\"[^\"]*\"[^>\"']*|'[^']*'[^>\"']*)*>", re.S
)


def visible_text(page: str) -> str:
    return html.unescape(TAGS.sub(" ", page))


def raw_numbers(page: str) -> list[str]:
    text = visible_text(page)
    return DOT_DECIMAL.findall(text) + TRAILING_ZEROS.findall(text)


@pytest.fixture
def demo():
    load_demo()
    recalculate_all()


def test_no_raw_numbers_on_admin_pages(client, admin_user, demo):
    client.force_login(admin_user)
    problems = {}
    for model, model_admin in admin.site._registry.items():
        if model._meta.app_label not in OUR_APPS:
            continue
        prefix = f"admin:{model._meta.app_label}_{model._meta.model_name}"
        urls = [reverse(f"{prefix}_changelist")]
        obj = model._default_manager.first()
        if obj is not None:
            urls.append(reverse(f"{prefix}_change", args=[obj.pk]))
        if model_admin.has_add_permission(_request(client, admin_user)):
            urls.append(reverse(f"{prefix}_add"))
        for url in urls:
            response = client.get(url, follow=True)
            assert response.status_code == 200, url
            found = raw_numbers(response.content.decode())
            if found:
                problems[url] = sorted(set(found))[:8]
    assert not problems, "Сырые числа на страницах: " + "; ".join(
        f"{url}: {nums}" for url, nums in problems.items()
    )


def _request(client, user):
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.user = user
    return request
