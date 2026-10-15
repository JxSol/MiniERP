"""Склад: проводки документов, запрет минуса, отмена, несколько складов (ТЗ, раздел 13)."""

from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError

from catalog.models import Equipment, Material
from core.demo import load_demo, product_of, variant_of
from costing.services import recalculate_all
from production.models import Part
from stock.models import (
    Assembly,
    DocumentStatus,
    Inventory,
    InventoryLine,
    ItemKind,
    Packing,
    ProductionRun,
    Receipt,
    ReceiptLine,
    Shortage,
    Transfer,
    TransferLine,
    Warehouse,
    balance_of,
    total_of,
)
from stock.reports import fact_scrap_percent, low_stock_materials

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo():
    data = load_demo()
    recalculate_all()
    # демо уже заполнило склад «Цех»; тесты работают на пустых складах
    data["wh"] = Warehouse.objects.create(name="Тестовый")
    data["shop"] = Warehouse.objects.create(name="Второй")
    return data


def mat(name, color=None):
    return (
        Material.objects.get(name=name, color__name=color)
        if color
        else Material.objects.get(name=name)
    )


def count(wh, counted: dict):
    """Инвентаризация: {позиция: количество}. Позиция — материал, (деталь, материал), вариант или товар."""
    inv = Inventory.objects.create(warehouse=wh, notes="начальные остатки")
    for item, qty in counted.items():
        if isinstance(item, tuple):
            InventoryLine.objects.create(
                inventory=inv, part=item[0], part_material=item[1], counted_qty=D(qty)
            )
        elif isinstance(item, Material):
            InventoryLine.objects.create(inventory=inv, material=item, counted_qty=D(qty))
        elif hasattr(item, "coloring"):
            InventoryLine.objects.create(inventory=inv, variant=item, counted_qty=D(qty))
        else:
            InventoryLine.objects.create(inventory=inv, product=item, counted_qty=D(qty))
    inv.post()
    return inv


def material_balance(wh, material):
    return balance_of(wh.pk, (ItemKind.MATERIAL, material.pk, None))


def part_balance(wh, part, material):
    return balance_of(wh.pk, (ItemKind.PART, part.pk, material.pk))


# --- Приход ---


def test_receipt_adds_stock_and_updates_offer_price(demo):
    wh = demo["wh"]
    white = mat("PLA Basic", "Белый")
    offer = white.offers.get()
    receipt = Receipt.objects.create(warehouse=wh)
    ReceiptLine.objects.create(
        receipt=receipt,
        material=white,
        offer=offer,
        packs=D(2),
        pack_size=D(1000),
        pack_price=D(9500),
    )
    receipt.post()
    receipt.refresh_from_db()
    assert receipt.status == DocumentStatus.POSTED and receipt.number.startswith("BUY-")
    assert material_balance(wh, white) == D(2000)
    offer.refresh_from_db()
    assert offer.pack_price == D(9500)
    assert offer.price_history.count() == 2
    # повторное проведение не удваивает остаток
    receipt.post()
    assert material_balance(wh, white) == D(2000)


# --- Выпуск деталей ---


def test_production_run_consumes_material_incl_scrap(demo):
    wh = demo["wh"]
    white = mat("PLA Basic", "Белый")
    shade = Part.objects.get(name="Плафон Луна")
    count(wh, {white: 1000})
    run = ProductionRun(
        warehouse=wh, part=shade, material=white, equipment=shade.equipment, good_qty=2, scrap_qty=1
    )
    run.full_clean()
    run.save()
    run.post()
    assert material_balance(wh, white) == D(250)  # 1000 − 250 × 3
    assert part_balance(wh, shade, white) == D(2)
    assert fact_scrap_percent(part=shade) == D(100) / 3  # в демо у плафона та же пропорция 2:1
    assert fact_scrap_percent(equipment=shade.equipment) is not None


def test_production_run_rejects_shortage(demo):
    wh = demo["wh"]
    white = mat("PLA Basic", "Белый")
    shade = Part.objects.get(name="Плафон Луна")
    count(wh, {white: 300})
    run = ProductionRun(
        warehouse=wh, part=shade, material=white, equipment=shade.equipment, good_qty=2
    )
    with pytest.raises(ValidationError, match="есть 300 г, нужно 500 г"):
        run.full_clean()
    run.save()
    with pytest.raises(Shortage):
        run.post()
    assert run.status == DocumentStatus.DRAFT
    assert not run._moves_qs().exists()


def test_production_run_defaults_from_part(demo):
    wh = demo["wh"]
    shade = Part.objects.get(name="Плафон Луна")
    count(wh, {shade.material: 1000})
    run = ProductionRun(warehouse=wh, part=shade, good_qty=1)
    run.full_clean()
    assert run.material == shade.material and run.equipment == shade.equipment
    plywood = mat("Фанера 4 мм")
    bad = ProductionRun(warehouse=wh, part=shade, material=plywood, good_qty=1)
    with pytest.raises(ValidationError, match="делается из"):
        bad.full_clean()


# --- Сборка ---


def test_assembly_consumes_parts_by_slot_and_components(demo):
    wh, luna = demo["wh"], demo["luna"]
    gold = mat("PLA Silk", "Золотой")
    shade, base, fix = (
        Part.objects.get(name=n) for n in ("Плафон Луна", "Подставка Луна", "Фиксатор плафона")
    )
    plywood, cord, socket, screw = (
        mat(n) for n in ("Фанера 4 мм", "Шнур с выключателем", "Патрон E27", "Винт M3")
    )
    count(wh, {(shade, gold): 1, (base, plywood): 1, (fix, gold): 2, cord: 1, socket: 1, screw: 4})
    golden = variant_of(luna, "Золотой")
    white_v = variant_of(luna, "Белый")
    # белой «Луны» не собрать — нет белых плафонов
    with pytest.raises(ValidationError, match="Плафон Луна \\(PLA Basic, белый\\)"):
        Assembly(warehouse=wh, variant=white_v, quantity=1).full_clean()
    asm = Assembly.objects.create(warehouse=wh, variant=golden, quantity=1)
    asm.post()
    assert balance_of(wh.pk, (ItemKind.VARIANT, golden.pk, None)) == D(1)
    assert part_balance(wh, shade, gold) == 0 and part_balance(wh, fix, gold) == 0
    assert material_balance(wh, screw) == 0 and material_balance(wh, cord) == 0
    with pytest.raises(Shortage):
        Assembly.objects.create(warehouse=wh, variant=golden, quantity=1).post()


def test_random_variant_assembly_takes_any_color(demo):
    wh, ball = demo["wh"], demo["ball"]
    red, green = mat("PLA Basic", "Красный"), mat("PLA Basic", "Зелёный")
    body = Part.objects.get(name="Корпус шара 6 см")
    count(wh, {(body, red): 3, (body, green): 2})
    mix = variant_of(ball, "Ассорти")
    Assembly.objects.create(warehouse=wh, variant=mix, quantity=4).post()
    assert part_balance(wh, body, red) == 0 and part_balance(wh, body, green) == D(1)
    with pytest.raises(Shortage):
        Assembly.objects.create(warehouse=wh, variant=mix, quantity=2).post()


# --- Упаковка ---


def test_packing_consumes_variants_and_packaging(demo):
    wh, ball = demo["wh"], demo["ball"]
    red = variant_of(ball, "Красный")
    small_box = mat("Коробочка 8×8×8")
    product = product_of(ball, "Красный")
    count(wh, {red: 5, small_box: 3})
    Packing.objects.create(warehouse=wh, product=product, quantity=3).post()
    assert balance_of(wh.pk, (ItemKind.PRODUCT, product.pk, None)) == D(3)
    assert balance_of(wh.pk, (ItemKind.VARIANT, red.pk, None)) == D(2)
    assert material_balance(wh, small_box) == 0
    with pytest.raises(ValidationError, match="Коробочка"):
        Packing(warehouse=wh, product=product, quantity=1).full_clean()


# --- Перемещение, несколько складов, инвентаризация ---


def test_transfer_and_totals_across_warehouses(demo):
    wh, shop = demo["wh"], demo["shop"]
    green = mat("PLA Basic", "Зелёный")  # зелёного в демо-остатках нет
    count(wh, {green: 1000})
    tr = Transfer.objects.create(warehouse=wh, to_warehouse=shop)
    TransferLine.objects.create(transfer=tr, material=green, quantity=D(400))
    tr.post()
    assert material_balance(wh, green) == D(600) and material_balance(shop, green) == D(400)
    assert total_of((ItemKind.MATERIAL, green.pk, None)) == D(1000)
    with pytest.raises(ValidationError, match="совпадают"):
        Transfer(warehouse=wh, to_warehouse=wh).full_clean()


def test_inventory_sets_exact_balance(demo):
    wh = demo["wh"]
    white = mat("PLA Basic", "Белый")
    count(wh, {white: 1000})
    inv = count(wh, {white: 640})
    line = inv.lines.get()
    assert line.difference == D(-360)
    assert material_balance(wh, white) == D(640)


def test_cancel_restores_stock_and_refuses_when_it_would_go_negative(demo):
    wh = demo["wh"]
    white = mat("PLA Basic", "Белый")
    shade = Part.objects.get(name="Плафон Луна")
    receipt = count(wh, {white: 500})
    run = ProductionRun.objects.create(
        warehouse=wh, part=shade, material=white, equipment=shade.equipment, good_qty=1
    )
    run.post()
    with pytest.raises(Shortage):  # без прихода выпуск ушёл бы в минус
        receipt.cancel()
    run.cancel()
    assert run.status == DocumentStatus.CANCELLED
    assert material_balance(wh, white) == D(500) and part_balance(wh, shade, white) == 0
    receipt.cancel()
    assert material_balance(wh, white) == 0


def test_low_stock_report(demo):
    wh = demo["wh"]
    green = mat("PLA Basic", "Зелёный")  # зелёного в демо-остатках нет
    green.min_stock = D(500)
    green.save()
    count(wh, {green: 300})
    assert [(m.pk, q) for m, q in low_stock_materials()] == [(green.pk, D(300))]
    count(wh, {green: 900})
    assert low_stock_materials() == []


def test_item_validation(demo):
    inv = Inventory.objects.create(warehouse=demo["wh"])
    with pytest.raises(ValidationError, match="ровно одну позицию"):
        InventoryLine(inventory=inv, counted_qty=D(1)).full_clean()
    shade = Part.objects.get(name="Плафон Луна")
    with pytest.raises(ValidationError, match="укажите материал"):
        InventoryLine(inventory=inv, part=shade, counted_qty=D(1)).full_clean()
    assert Equipment.objects.count() == 2
