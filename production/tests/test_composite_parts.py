"""
Композитная деталь (несколько материалов через АМС), цветовые группы в детали, расцветки
на складе, выпуск с фактом и «Сделать нормой детали».
"""

from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from catalog.models import Equipment, Material
from core.demo import load_demo
from costing.services import part_tree, recalculate_all, variant_tree
from production import generators
from production.models import (
    ModelPart,
    Part,
    PartColoring,
    PartMaterial,
    ProductCategory,
    ProductModel,
    Variant,
    slot_conflicts,
)
from stock.models import (
    Assembly,
    ItemKind,
    ProductionRun,
    Receiving,
    ReceivingLine,
    StockReason,
    Warehouse,
    balance_of,
)

pytestmark = pytest.mark.django_db

NBSP = "\u00a0"


def mat(name, color):
    return Material.objects.get(name=name, color__name=color)


@pytest.fixture
def demo():
    data = load_demo()
    recalculate_all()
    data["wh"] = Warehouse.objects.create(name="Тестовый")
    return data


@pytest.fixture
def figurine(demo):
    """Фигурка: корпус из чёрного PLA (группа «Основной») и глаза из белого (группа «Глаза»)."""
    printer = Equipment.objects.get(name="Bambu P1S")
    part = Part.objects.create(
        name="Фигурка кот", equipment=printer, batch_minutes=D(300), batch_size=4
    )
    PartMaterial.objects.create(
        part=part,
        order=1,
        name="Корпус",
        material=mat("PLA Basic", "Чёрный"),
        batch_qty=D(200),
        slot="Основной",
    )
    PartMaterial.objects.create(
        part=part,
        order=2,
        name="Глаза",
        material=mat("PLA Basic", "Белый"),
        batch_qty=D(8),
        slot="Глаза",
    )
    model = ProductModel.objects.create(
        name="Кот", category=ProductCategory.objects.get(name="Ёлочные игрушки")
    )
    ModelPart.objects.create(model=model, part=part, quantity=1)
    recalculate_all()
    return part, model


def test_composite_part_costs_and_weighs_both_materials(figurine):
    part, model = figurine
    part.refresh_from_db()
    tree = part_tree(part)
    materials = [n for n in tree.children if n.kind == "material"]
    assert [m.name for m in materials] == ["PLA Basic, чёрный", "PLA Basic, белый"]
    assert materials[0].quantity == D(50) and materials[1].quantity == D(2)  # на 1 шт: партия / 4
    assert part.grams_per_piece() == D(52)
    assert model.calculated_weight_g() == D(52)
    assert part.material == mat("PLA Basic", "Чёрный")  # первая позиция — «материал» в списках


def test_model_slots_come_from_part_positions(figurine):
    part, model = figurine
    assert model.slot_names() == ["Основной", "Глаза"]
    assert model.slot_default_materials()["Глаза"] == mat("PLA Basic", "Белый")
    variant = Variant.objects.create(model=model, name="Чёрный с белыми глазами")
    assert {s.slot: s.material.color.name for s in variant.slots.all()} == {
        "Основной": "Чёрный",
        "Глаза": "Белый",
    }
    # вариант сразу знает расцветку своей детали
    assert part.colorings.get().label == "Корпус: PLA Basic, чёрный + Глаза: PLA Basic, белый"
    # переименовали группу в детали — у модели и вариантов она тоже переименовалась
    eyes = part.materials.get(name="Глаза")
    eyes.slot = "Акцент"
    eyes.save()
    assert model.slot_names() == ["Основной", "Акцент"]
    assert set(variant.slots.values_list("slot", flat=True)) == {"Основной", "Акцент"}


def test_slot_conflict_between_parts_of_different_categories(demo):
    shade = Part.objects.get(name="Плафон Луна")
    base = Part.objects.get(name="Подставка Луна")  # фанера, без группы
    assert slot_conflicts([shade, base]) == []
    position = base.main_position
    position.slot = "Плафон"
    position.save()
    problems = slot_conflicts([shade, Part.objects.get(pk=base.pk)])
    assert len(problems) == 1 and "Плафон" in problems[0] and "Фанера" in problems[0]


def test_variant_colors_cost_and_coloring_on_stock(figurine):
    part, model = figurine
    wh = Warehouse.objects.get(name="Тестовый")
    red_eyes = Variant.objects.create(model=model, name="Чёрный с красными глазами")
    red_eyes.slots.filter(slot="Глаза").update(material=mat("PLA Basic", "Красный"))
    recalculate_all()
    tree = variant_tree(red_eyes)
    names = [n.name for n in tree.find("material")]
    assert "PLA Basic, красный" in names and "PLA Basic, белый" not in names

    # выпуск: глаза печатали красным, корпус по умолчанию; расход глаз — по факту
    black, red, white = (mat("PLA Basic", c) for c in ("Чёрный", "Красный", "Белый"))
    receiving = Receiving.objects.create(
        warehouse=wh, reason=StockReason.objects.get(name="Начальные остатки")
    )
    for material in (black, red):
        ReceivingLine.objects.create(document=receiving, material=material, quantity=D(1000))
    receiving.post()
    run = ProductionRun.objects.create(warehouse=wh, part=part, good_qty=3, scrap_qty=1)
    run.full_clean()
    run.materials.create(position=part.materials.get(name="Глаза"), material=red, quantity=D(10))
    run.post()
    assert balance_of(wh.pk, (ItemKind.MATERIAL, black.pk, None)) == D(800)  # 200 на 4 шт по норме
    assert balance_of(wh.pk, (ItemKind.MATERIAL, red.pk, None)) == D(990)  # факт 10 г
    assert balance_of(wh.pk, (ItemKind.MATERIAL, white.pk, None)) == 0
    made = PartColoring.get_for(part, {part.materials.get(name="Глаза").pk: red})
    assert made.label == "Корпус: PLA Basic, чёрный + Глаза: PLA Basic, красный"
    assert balance_of(wh.pk, (ItemKind.PART, part.pk, made.pk)) == D(3)
    assert run.materials_summary() == (
        f"PLA Basic, чёрный: 200{NBSP}г (по норме); PLA Basic, красный: 10{NBSP}г (норма 8{NBSP}г)"
    )

    # сборка варианта с белыми глазами не найдёт деталей: на складе только с красными
    white_eyes = Variant.objects.create(model=model, name="Чёрный с белыми глазами")
    with pytest.raises(ValidationError, match="Глаза: PLA Basic, белый"):
        Assembly(warehouse=wh, variant=white_eyes, quantity=1).full_clean()
    Assembly.objects.create(warehouse=wh, variant=red_eyes, quantity=2).post()
    assert balance_of(wh.pk, (ItemKind.PART, part.pk, made.pk)) == D(1)


def test_run_can_become_the_norm(figurine):
    part, _ = figurine
    part.refresh_from_db()  # себестоимость по норме посчитана после создания детали
    wh = Warehouse.objects.get(name="Тестовый")
    black, white = mat("PLA Basic", "Чёрный"), mat("PLA Basic", "Белый")
    receiving = Receiving.objects.create(
        warehouse=wh, reason=StockReason.objects.get(name="Начальные остатки")
    )
    for material in (black, white):
        ReceivingLine.objects.create(document=receiving, material=material, quantity=D(1000))
    receiving.post()
    run = ProductionRun.objects.create(
        warehouse=wh, part=part, good_qty=6, scrap_qty=0, actual_minutes=D(400)
    )
    run.ensure_materials()
    run.materials.filter(position__name="Корпус").update(quantity=D(330))
    run.post()
    run.refresh_from_db()
    assert run.norm_cost_per_piece == part.cost_cached.quantize(D("0.01"))
    assert run.cost_per_piece < run.norm_cost_per_piece  # 6 штук за 400 мин дешевле нормы 4 за 300
    run.apply_as_norm()
    part.refresh_from_db()
    assert part.batch_size == 6 and part.batch_minutes == D(400)
    assert part.materials.get(name="Корпус").batch_qty == D(330)
    assert part.materials.get(name="Глаза").batch_qty == D(12)  # по норме: 2 г × 6


def test_part_admin_form_and_run_form_pages(client, admin_user, figurine):
    part, _ = figurine
    client.force_login(admin_user)
    add_page = client.get(reverse("admin:production_part_add")).content.decode()
    assert 'value="Основной"' in add_page  # группа первой позиции подставлена
    printer = Equipment.objects.get(name="Bambu P1S")
    response = client.post(
        reverse("admin:production_part_add"),
        {
            "name": "Подвеска",
            "equipment": printer.pk,
            "batch_minutes_0": "0",
            "batch_minutes_1": "1",
            "batch_minutes_2": "0",
            "batch_minutes_3": "0",
            "batch_size": "10",
            "is_active": "on",
            "notes": "",
            "materials-TOTAL_FORMS": "2",
            "materials-INITIAL_FORMS": "0",
            "materials-MIN_NUM_FORMS": "1",
            "materials-0-name": "",
            "materials-0-material": mat("PLA Basic", "Зелёный").pk,
            "materials-0-batch_qty": "100",
            "materials-0-batch_unit": "",
            "materials-0-slot": "Основной",
            "materials-1-name": "Петля",
            "materials-1-material": mat("PLA Silk", "Золотой").pk,
            "materials-1-batch_qty": "5",
            "materials-1-batch_unit": "",
            "materials-1-slot": "",
            "operations-TOTAL_FORMS": "0",
            "operations-INITIAL_FORMS": "0",
            "photos-TOTAL_FORMS": "0",
            "photos-INITIAL_FORMS": "0",
        },
    )
    assert response.status_code == 302, response.content.decode()[:800]
    pendant = Part.objects.get(name="Подвеска")
    assert [(p.order, p.label, p.slot) for p in pendant.positions] == [
        (1, "Материал", "Основной"),
        (2, "Петля", ""),
    ]
    page = client.get(reverse("admin:production_part_change", args=[part.pk])).content.decode()
    assert "Корпус" in page and "Глаза" in page and "Материалы на типовую партию" in page
    run_page = client.get(reverse("admin:stock_productionrun_add")).content.decode()
    assert "run_materials.js" in run_page
    info = client.get(reverse("admin:stock_productionrun_part_info", args=[part.pk])).json()
    assert [p["label"] for p in info["positions"]] == ["Корпус", "Глаза"]
    assert info["positions"][0]["per_piece"] == "50"


def test_copy_part_keeps_positions(figurine):
    part, _ = figurine
    copy = generators.copy_part(part)
    assert [(p.name, p.slot, p.batch_qty) for p in copy.positions] == [
        ("Корпус", "Основной", D(200)),
        ("Глаза", "Глаза", D(8)),
    ]
