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
from stock.models import DocumentStatus, ItemKind, ProductionRun, Receipt, Warehouse, balance_of

pytestmark = pytest.mark.django_db

MODELS = ["receipt", "productionrun", "assembly", "packing", "transfer", "inventory", "warehouse"]


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
