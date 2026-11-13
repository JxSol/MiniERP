"""Формы учёта материалов: штучный, листовой, рулонный (ТЗ п. 6.2) — цены, единицы, остатки."""

from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.forms.models import modelform_factory
from django.urls import reverse

from catalog.models import Material, MaterialCategory, MaterialForm, MaterialOffer, QtyUnit, Unit
from core.demo import load_demo
from costing.services import recalculate_all
from production.models import Part, PartMaterial
from sales.admin import ProductPackagingForm
from sales.models import Product
from stock.admin import ITEM_FIELDS, ItemLineForm
from stock.models import (
    ItemKind,
    Receipt,
    ReceiptLine,
    StockReason,
    Warehouse,
    WriteOff,
    WriteOffLine,
    balance_of,
)

pytestmark = pytest.mark.django_db

NBSP = "\u00a0"


@pytest.fixture
def demo():
    load_demo()
    recalculate_all()


def material(name, form_type, **dims):
    m = Material(
        name=name,
        category=MaterialCategory.objects.get(name="Транспортная"),
        form_type=form_type,
        unit=Unit.PIECE,
        **dims,
    )
    m.full_clean(exclude=["name_key"])  # clean() подставит единицу расхода по форме
    m.save()
    return m


def prices(m):
    return [(p.quantize(D("0.01")), u) for p, u in m.unit_prices()]


def test_roll_material_prices_and_units():
    bubble = material("ВПП плёнка 120 см", MaterialForm.ROLL, width_cm=D(120), roll_length_m=D(100))
    assert bubble.unit == Unit.LINEAR_METER  # рулон считается в погонных метрах
    assert bubble.roll_area_m2 == D(120)
    MaterialOffer.objects.create(
        material=bubble, pack_size=bubble.roll_length_m, pack_price=D(6000)
    )
    assert prices(bubble) == [(D("60.00"), "пог. м"), (D("50.00"), "м²")]
    assert bubble.to_base(D(6), QtyUnit.SQ_METER) == D(5)  # 6 м² при ширине 1,2 м = 5 пог. м
    assert bubble.from_base(D(5), QtyUnit.SQ_METER) == D(6)
    assert bubble.format_qty(D(500)) == f"500{NBSP}пог. м (600{NBSP}м²)"
    assert bubble.format_entered(D(50), QtyUnit.SQ_METER) == f"60{NBSP}м² (50{NBSP}пог. м)"
    assert bubble.direct_unit == ("пог. м", D(1))


def test_sheet_material_prices_and_units():
    film = material(
        "Плёнка упаковочная 58×58 см", MaterialForm.SHEET, width_cm=D(58), length_cm=D(58)
    )
    assert film.unit == Unit.SHEET
    assert film.sheet_area_m2 == D("0.3364")
    MaterialOffer.objects.create(material=film, pack_size=D(10), pack_price=D(600))
    assert prices(film) == [(D("60.00"), "лист"), (D("178.36"), "м²")]
    assert film.to_base(D("0.3364"), QtyUnit.SQ_METER) == D(1)
    assert film.format_qty(D(10)) == f"10{NBSP}лист (3,364{NBSP}м²)"


def test_piece_material_volume_and_prices():
    box = material(
        "Коробка 30×30×100 см",
        MaterialForm.PIECE,
        width_cm=D(30),
        length_cm=D(100),
        height_cm=D(30),
    )
    assert box.unit == Unit.PIECE
    assert box.piece_volume_m3 == D("0.09")
    MaterialOffer.objects.create(material=box, pack_size=D(10), pack_price=D(600))
    assert prices(box) == [(D("60.00"), "шт"), (D("666.67"), "м³")]
    assert box.format_qty(D(10)) == f"10{NBSP}шт (0,9{NBSP}м³)"
    with pytest.raises(ValidationError, match="вводится"):
        box.to_base(D(1), QtyUnit.SQ_METER)  # коробки списываются только поштучно


def test_zero_price_is_rejected(demo):
    """Нулевая цена не делает материал бесплатным: её не принимают ни предложение, ни закупка."""
    bubble = Material.objects.get(name="ВПП плёнка 120 см")
    offer = MaterialOffer(material=bubble, pack_size=D(100), pack_price=D(0))
    with pytest.raises(ValidationError):
        offer.full_clean()
    position = PartMaterial(part=Part(name="Пылинка", batch_size=10), batch_qty=D("0.0001"))
    with pytest.raises(ValidationError, match="меньше 0,0001"):
        position.clean()
    with pytest.raises(ValidationError, match="получается 0"):
        bubble.to_base(D("0.00001"), QtyUnit.SQ_METER)


def test_sheet_and_roll_require_dimensions():
    sheet = Material(
        name="Лист без размеров",
        category=MaterialCategory.objects.get(name="Транспортная"),
        form_type=MaterialForm.SHEET,
        unit=Unit.SHEET,
    )
    with pytest.raises(ValidationError, match="ширину и длину"):
        sheet.full_clean(exclude=["name_key"])
    roll = Material(
        name="Рулон без ширины",
        category=MaterialCategory.objects.get(name="Транспортная"),
        form_type=MaterialForm.ROLL,
        unit=Unit.SQ_METER,
    )
    with pytest.raises(ValidationError, match="ширину рулона"):
        roll.full_clean(exclude=["name_key"])


def test_packaging_entered_in_square_meters(demo):
    bubble = Material.objects.get(name="ВПП плёнка 120 см")
    product = Product.objects.get(name="Светильник Луна — Белый")
    form = ProductPackagingForm(
        data={"material": bubble.pk, "quantity": "0.6", "entered_unit": "m2"}
    )
    assert form.is_valid(), form.errors
    line = form.save(commit=False)
    line.product = product
    line.save()
    assert line.quantity == D("0.5")  # 0,6 м² / ширина 1,2 м = 0,5 пог. м
    assert str(line) == f"ВПП плёнка 120 см: 0,6{NBSP}м² (0,5{NBSP}пог. м)"
    assert ProductPackagingForm(instance=line).initial["quantity"] == D("0.6")
    wrong = ProductPackagingForm(
        data={
            "material": Material.objects.get(name="Коробка 20×20×25").pk,
            "quantity": "1",
            "entered_unit": "m2",
        }
    )
    assert not wrong.is_valid() and "entered_unit" in wrong.errors


def test_roll_receipt_and_writeoff(demo):
    """Приход 5 рулонов = 500 пог. м; списание 60 м² = 50 пог. м → остаток 450 пог. м."""
    bubble = Material.objects.get(name="ВПП плёнка 120 см")
    wh = Warehouse.objects.create(name="Тестовый")
    key = (ItemKind.MATERIAL.value, bubble.pk, None)
    receipt = Receipt.objects.create(warehouse=wh)
    offer = bubble.offers.get()
    assert offer.pack_size == D(100)  # рулон = 100 пог. м
    line = ReceiptLine.objects.create(
        receipt=receipt, material=bubble, supplier=offer.supplier, packs=5, total_price=D(30000)
    )
    assert line.pack_size == D(100) and line.pack_price == D(6000)  # упаковка из предложения
    assert line.quantity == D(500)
    assert line.summary().startswith(f"5 уп. × 100{NBSP}пог. м (120{NBSP}м²) = 500{NBSP}пог. м")
    receipt.post()
    assert balance_of(wh.pk, key) == D(500)

    LineForm = modelform_factory(
        WriteOffLine, form=ItemLineForm, fields=(*ITEM_FIELDS, "quantity", "entered_unit")
    )
    doc = WriteOff.objects.create(warehouse=wh, reason=StockReason.objects.get(name="Прочее"))
    form = LineForm(
        data={"material": bubble.pk, "quantity": "60", "entered_unit": "m2"},
        instance=WriteOffLine(document=doc),
    )
    assert form.is_valid(), form.errors
    line = form.save()
    assert line.quantity == D(50)
    assert line.quantity_display() == f"60{NBSP}м² (50{NBSP}пог. м)"
    doc.post()
    assert balance_of(wh.pk, key) == D(450)


def test_material_pages_show_forms(client, admin_user, demo):
    client.force_login(admin_user)
    bubble = Material.objects.get(name="ВПП плёнка 120 см")
    html = client.get(reverse("admin:catalog_material_change", args=[bubble.pk])).content.decode()
    assert "Форма учёта" in html and "Длина рулона" in html
    assert "60" in html and "пог. м" in html
    listing = client.get(reverse("admin:stock_stockbalance_changelist")).content.decode()
    assert f"500{NBSP}пог. м (600{NBSP}м²)" in listing
    assert f"10{NBSP}лист (3,364{NBSP}м²)" in listing


def test_unit_price_is_shown_with_two_decimals(client, admin_user, demo):
    """Катушка 1000 г за 4 588 ₸: на экране «4,59 ₸/г», а в расчёты идёт точная цена 4,588."""
    green = Material.objects.get(name="PLA Basic", color__name="Зелёный")
    offer = green.offers.get()
    offer.pack_size, offer.pack_price = D(1000), D(4588)
    offer.save()
    assert Material.objects.get(pk=green.pk).unit_price == D("4.588")

    client.force_login(admin_user)
    listing = client.get(reverse("admin:catalog_material_changelist")).content.decode()
    assert f"4,59{NBSP}₸/г" in listing and "4,588" not in listing

    line = ReceiptLine.objects.create(
        receipt=Receipt.objects.create(warehouse=Warehouse.objects.create(name="Тестовый")),
        material=green,
        supplier=offer.supplier,
        manufacturer=offer.manufacturer,
        packs=2,
        total_price=D(9176),
    )
    assert line.summary() == (f"2 уп. × 1{NBSP}кг = 2{NBSP}кг; 9{NBSP}176{NBSP}₸ (4,59{NBSP}₸/г)")


def test_material_cipher_normalized_unique_and_searchable(client, admin_user, demo):
    """Шифр: три знака 0–9/A–F, кириллица и строчные приводятся, повтор не сохраняется, поиск работает."""
    from django.urls import reverse

    from catalog.models import next_free_cipher

    pla = Material.objects.get(name="PLA Basic", color__name="Белый")
    pla.cipher = "01а"  # строчная кириллическая «а»
    pla.full_clean(exclude=["name_key"])
    pla.save()
    assert pla.cipher == "01A"
    assert next_free_cipher() == "001"

    other = Material.objects.get(name="PLA Basic", color__name="Чёрный")
    other.cipher = "01A"
    with pytest.raises(ValidationError):
        other.full_clean(exclude=["name_key"])
    other.cipher = "01G"
    with pytest.raises(ValidationError, match="ровно три знака"):
        other.full_clean(exclude=["name_key"])

    client.force_login(admin_user)
    html = client.get(reverse("admin:catalog_material_changelist") + "?q=01A").content.decode()
    assert "PLA Basic, белый" in html and "PLA Basic, чёрный" not in html
    add_page = client.get(reverse("admin:catalog_material_add")).content.decode()
    assert 'data-cipher="001"' in add_page and "Подставить 001" in add_page
    edit_page = client.get(reverse("admin:catalog_material_change", args=[pla.pk])).content.decode()
    assert "Подставить" not in edit_page  # у материала шифр уже есть


def test_color_hex_widget_and_normalization(client, admin_user):
    """Палитра: рядом с полем HEX есть выбор цвета, а ввод «388e3c» и «#38c» приводится к #RRGGBB."""
    from django.urls import reverse

    from catalog.admin import normalize_hex
    from catalog.models import Color

    assert normalize_hex(" 388e3c ") == "#388E3C"
    assert normalize_hex("#38c") == "#3388CC"
    assert normalize_hex("#388E3C") == "#388E3C"
    client.force_login(admin_user)
    page = client.get(reverse("admin:catalog_color_add")).content.decode()
    assert 'type="color"' in page and 'name="hex"' in page and "pipette" not in page
    response = client.post(
        reverse("admin:catalog_color_add"),
        {"name": "Проверочный", "hex": "388e3c", "order": "99", "is_active": "on", "notes": ""},
    )
    assert response.status_code == 302, response.content.decode()[:500]
    assert Color.objects.get(name="Проверочный").hex == "#388E3C"
