"""
Удаление новой строки в таблицах карточек (core/inline_delete.js). Тема при удалении
пересоздаёт строки ниже и теряет введённое в них, поэтому удаление у нас своё. Сам скрипт
проверяется в браузере; здесь — что он подключён и что тема устроена так, как он ожидает.
"""

from pathlib import Path

import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse

from core.demo import load_demo
from production.models import ProductModel

pytestmark = pytest.mark.django_db


def test_row_delete_script_is_on_pages_with_tables(client, admin_user):
    assert finders.find("core/inline_delete.js")
    load_demo()
    client.force_login(admin_user)
    model = ProductModel.objects.get(name="Светильник Луна")
    for url in (
        reverse("admin:stock_receipt_add"),
        reverse("admin:production_productmodel_change", args=[model.pk]),
    ):
        page = client.get(url).content.decode()
        assert "core/inline_delete.js" in page, url
        # то, по чему скрипт находит строку, её таблицу и счётчики строк
        assert "delete-template" in page, url
        assert 'class="formset-wrapper' in page, url
        assert "data-inline-formset=" in page, url
        for counter in ("TOTAL_FORMS", "MIN_NUM_FORMS", "MAX_NUM_FORMS"):
            assert f'-{counter}"' in page, (url, counter)


def test_theme_is_what_the_script_expects():
    """
    Проверка на случай обновления темы. Скрипт зовёт три её функции и перехватывает щелчок
    по корзине, потому что её собственный обработчик пересоздаёт строки через innerHTML.
    """
    theme = Path(finders.find("unfold/js/app.js")).read_text(encoding="utf-8")
    script = Path(finders.find("core/inline_delete.js")).read_text(encoding="utf-8")
    for name in ("applyLastClass", "checkFormsetEmpty", "updateStackedCounter"):
        assert f"function {name}(" in theme, f"в теме больше нет функции {name}"
        assert f'theme("{name}"' in script
    assert '"formset:removed"' in theme and '"formset:removed"' in script
    assert "content.innerHTML = content.innerHTML.replaceAll(" in theme, (
        "Тема больше не пересоздаёт строки при удалении: проверьте, нужен ли ещё "
        "core/inline_delete.js"
    )
