"""
Время: в базе хранится в UTC, а показывается и считается по Алматы (UTC+5). Запись,
сделанная ночью, не должна уходить во вчерашний день ни в одном месте.
"""

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from catalog.models import Material, MaterialPriceHistory
from core.admin_utils import fmt_date, fmt_datetime
from core.demo import load_demo
from costing.services import fix_cost, recalculate_all
from sales.models import CostSnapshot, Product
from stock.models import Receipt

# 03:26 ночи 14 ноября в Алматы; в UTC это ещё 13 ноября, 22:26
NIGHT = datetime(2026, 11, 13, 22, 26, tzinfo=UTC)

OUR_CODE = ("core", "catalog", "production", "sales", "gallery", "stock", "costing", "config")


@pytest.fixture
def demo():
    load_demo()
    recalculate_all()


def test_almaty_is_utc_plus_five():
    """С марта 2024 года Алматы живёт по UTC+5; устаревшая база часовых поясов дала бы +6."""
    assert settings.TIME_ZONE == "Asia/Almaty" and settings.USE_TZ
    assert timezone.localtime(NIGHT).utcoffset() == timedelta(hours=5)


def test_dates_are_shown_in_local_time():
    assert fmt_date(NIGHT) == "14.11.2026"
    assert fmt_datetime(NIGHT) == "14.11.2026 03:26"
    assert fmt_date(date(2026, 11, 14)) == "14.11.2026"  # дата без времени — как есть
    assert fmt_date(None) == "—" and fmt_datetime(None) == "—"


def test_dates_are_formatted_in_one_place():
    """Дату в текст превращают только fmt_date и fmt_datetime: в них перевод в местное время."""
    root = Path(settings.BASE_DIR)
    raw = re.compile(r"strftime\(|%d\.%m|%H:%M")
    offenders = []
    for folder in OUR_CODE:
        for path in (root / folder).rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            if "/migrations/" in relative or "/tests/" in relative:
                continue
            if relative != "core/admin_utils.py" and raw.search(path.read_text(encoding="utf-8")):
                offenders.append(relative)
    assert not offenders, f"Дата форматируется в обход fmt_date: {offenders}"


@pytest.mark.django_db
def test_document_date_defaults_to_local_today():
    """Документ, созданный в 03:26 ночи, получает сегодняшнюю дату, а не вчерашнюю."""
    with mock.patch("django.utils.timezone.now", return_value=NIGHT):
        assert Receipt().date == date(2026, 11, 14)


@pytest.mark.django_db
def test_night_records_keep_their_date(client, admin_user, demo):
    white = Material.objects.get(name="PLA Basic", color__name="Белый")
    MaterialPriceHistory.objects.filter(material=white).update(changed_at=NIGHT)
    assert str(white.price_history.first()).endswith("от 14.11.2026")

    product = Product.objects.get(name="Светильник Луна — Белый")
    snapshot = fix_cost(product, admin_user)
    CostSnapshot.objects.filter(pk=snapshot.pk).update(created_at=NIGHT)
    snapshot.refresh_from_db()
    assert str(snapshot) == f"{product.sku} от 14.11.2026 03:26"

    client.force_login(admin_user)
    # колонка «Дата» в истории цен: время из базы Django переводит в местное сам
    card = client.get(reverse("admin:catalog_material_change", args=[white.pk])).content.decode()
    assert "14 ноября 2026 г. 3:26" in card and "13 ноября 2026" not in card
    # «Зафиксирована» в списке товаров
    listing = client.get(reverse("admin:sales_product_changelist")).content.decode()
    assert "(14.11.2026" in listing and "(13.11.2026" not in listing
