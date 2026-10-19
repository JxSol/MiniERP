"""Экраны склада открываются, записи проводятся из формы, продавцу склад недоступен."""

from decimal import Decimal as D

import pytest
from django.contrib.auth.models import Group, User
from django.urls import reverse

from catalog.models import Material
from core.demo import load_demo
from core.roles import SELLER, TECHNOLOGIST
from costing.services import recalculate_all
from production.models import Part
from stock.models import (
    DocumentStatus,
    ItemKind,
    ProductionRun,
    Receipt,
    StockBalance,
    StockMove,
    Warehouse,
    balance_of,
)

pytestmark = pytest.mark.django_db

NBSP = "\u00a0"

MODELS = [
    "receipt",
    "productionrun",
    "assembly",
    "packing",
    "transfer",
    "inventory",
    "receiving",
    "writeoff",
    "warehouse",
    "stockreason",
]


@pytest.fixture
def demo():
    data = load_demo()
    recalculate_all()
    data["wh"] = Warehouse.objects.create(name="Тестовый")
    return data


def user_in(group, username):
    user = User.objects.create_user(username, password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    return user


def test_stock_pages_open(client, admin_user, demo):
    client.force_login(admin_user)
    for name in MODELS:
        assert client.get(reverse(f"admin:stock_{name}_changelist")).status_code == 200, name
        assert client.get(reverse(f"admin:stock_{name}_add")).status_code == 200, name
    assert client.get(reverse("admin:stock_stockbalance_changelist")).status_code == 200
    assert client.get(reverse("admin:stock_stockmove_changelist")).status_code == 200


def test_receipt_and_run_posted_from_admin_forms(client, demo):
    client.force_login(user_in(TECHNOLOGIST, "tech"))
    wh = demo["wh"]
    white = Material.objects.get(name="PLA Basic", color__name="Белый")
    offer = white.offers.get()
    response = client.post(
        reverse("admin:stock_receipt_add"),
        {
            "date": "2026-10-15",
            "warehouse": wh.pk,
            "notes": "",
            "lines-TOTAL_FORMS": "1",
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "1",
            "lines-0-material": white.pk,
            "lines-0-offer": offer.pk,
            "lines-0-packs": "2",
            "lines-0-pack_size": "",
            "lines-0-pack_price": "",
        },
    )
    assert response.status_code == 302, response.content.decode()[:800]
    receipt = Receipt.objects.get(warehouse=wh)
    assert receipt.status == DocumentStatus.POSTED and receipt.created_by.username == "tech"
    assert receipt.lines.get().pack_size == D(1000)  # подставилось из предложения
    assert balance_of(wh.pk, (ItemKind.MATERIAL, white.pk, None)) == D(2000)

    shade = Part.objects.get(name="Плафон Луна")
    response = client.post(
        reverse("admin:stock_productionrun_add"),
        {
            "date": "2026-10-15",
            "warehouse": wh.pk,
            "part": shade.pk,
            "material": "",
            "equipment": "",
            "good_qty": "3",
            "scrap_qty": "1",
            "notes": "",
        },
    )
    assert response.status_code == 302, response.content.decode()[:800]
    run = ProductionRun.objects.get(warehouse=wh)
    assert run.material == white and run.equipment == shade.equipment
    assert run.status == DocumentStatus.POSTED
    assert balance_of(wh.pk, (ItemKind.MATERIAL, white.pk, None)) == D(1000)
    assert balance_of(wh.pk, (ItemKind.PART, shade.pk, white.pk)) == D(3)

    # нехватка — форма не сохраняется, остатки не меняются
    response = client.post(
        reverse("admin:stock_productionrun_add"),
        {
            "date": "2026-10-15",
            "warehouse": wh.pk,
            "part": shade.pk,
            "material": "",
            "equipment": "",
            "good_qty": "10",
            "scrap_qty": "0",
            "notes": "",
        },
    )
    assert response.status_code == 200 and "Не хватает" in response.content.decode()
    assert ProductionRun.objects.filter(warehouse=wh).count() == 1

    # отмена через действие
    response = client.post(
        reverse("admin:stock_productionrun_cancel_document", args=[run.pk]),
        {"_form_submitted": "on"},
        HTTP_HX_REQUEST="true",
    )
    assert response.status_code == 200
    run.refresh_from_db()
    assert run.status == DocumentStatus.CANCELLED
    assert balance_of(wh.pk, (ItemKind.MATERIAL, white.pk, None)) == D(2000)
    balances = client.get(reverse("admin:stock_stockbalance_changelist")).content.decode()
    assert "2 кг" in balances


def test_seller_has_no_stock_access(client, demo):
    client.force_login(user_in(SELLER, "seller"))
    assert client.get(reverse("admin:stock_stockbalance_changelist")).status_code == 403
    assert client.get(reverse("admin:stock_receipt_add")).status_code == 403


def test_balance_tabs_split_levels(client, admin_user, demo):
    """Остатки: без параметра — материалы; ?kind=part — только детали; вкладки видны."""
    client.force_login(admin_user)
    url = reverse("admin:stock_stockbalance_changelist")
    default = client.get(url)
    assert default.status_code == 200
    rows = list(default.context_data["cl"].result_list)
    assert rows and all(b.material_id for b in rows)
    html = default.content.decode()
    for kind, title in (("part", "Детали"), ("variant", "Изделия"), ("product", "Товары")):
        assert f"?kind={kind}" in html and title in html
    parts = client.get(url + "?kind=part")
    rows = list(parts.context_data["cl"].result_list)
    assert rows and all(b.part_id for b in rows)
    assert "min_display" in default.context_data["cl"].list_display
    assert "min_display" not in parts.context_data["cl"].list_display
    assert client.get(url + "?kind=nonsense").status_code == 200


def test_production_journal_tabs(client, admin_user, demo):
    client.force_login(admin_user)
    for name in ("productionrun", "assembly", "packing"):
        html = client.get(reverse(f"admin:stock_{name}_changelist")).content.decode()
        for title in ("Выпуск деталей", "Сборка изделий", "Упаковка товаров"):
            assert title in html, (name, title)


def test_adjustment_tabs_and_reason_choices(client, admin_user, demo):
    client.force_login(admin_user)
    for name in ("receiving", "writeoff"):
        html = client.get(reverse(f"admin:stock_{name}_changelist")).content.decode()
        assert "Оприходование" in html and "Списание" in html, name
    add_writeoff = client.get(reverse("admin:stock_writeoff_add")).content.decode()
    assert "Образцы и подарки" in add_writeoff and "Прочее" in add_writeoff
    assert "Начальные остатки" not in add_writeoff
    add_receiving = client.get(reverse("admin:stock_receiving_add")).content.decode()
    assert "Начальные остатки" in add_receiving and "Образцы и подарки" not in add_receiving


def test_showcase_columns_in_balances(client, admin_user, demo):
    client.force_login(admin_user)
    page = client.get(reverse("admin:stock_stockbalance_changelist") + "?kind=variant")
    columns = page.context_data["cl"].list_display
    assert "available_display" in columns and "showcase_display" in columns
    html = page.content.decode()
    assert "На витрине" in html and "Магазин — витрина" in html


def test_balance_and_move_cards_show_units_and_history(client, admin_user, demo):
    """Карточка остатка: позиция, склад, остаток с единицами, последние движения; без сырых полей."""
    client.force_login(admin_user)
    bubble = Material.objects.get(name="ВПП плёнка 120 см")
    balance = StockBalance.objects.get(material=bubble, warehouse__name="Цех")
    html = client.get(
        reverse("admin:stock_stockbalance_change", args=[balance.pk])
    ).content.decode()
    assert f"600{NBSP}м² (500{NBSP}пог. м)" in html
    assert "Последние движения" in html and "BUY-" in html
    assert "600,0000" not in html and "Материал детали" not in html
    assert chr(34).join(["title=", "Что это за поле", ""]) in html  # значок «i» с пояснением
    move = StockMove.objects.filter(material=bubble).first()
    page = client.get(reverse("admin:stock_stockmove_change", args=[move.pk])).content.decode()
    assert "Документ" in page and f"+600{NBSP}м²" in page
    assert str(balance).endswith(f"Цех: 600{NBSP}м² (500{NBSP}пог. м)")
