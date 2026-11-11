"""
Взаимозаменяемые категории материалов: связь зеркальная и не идёт по цепочке; материал
связанной категории принимают вариант, выпуск, цветовая группа модели и генератор вариантов;
связь, на которой держатся варианты, убрать нельзя.
"""

from decimal import Decimal as D

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from catalog.models import Color, Material, MaterialCategory, MaterialOffer, Unit
from core.demo import load_demo
from costing.services import recalculate_all, variant_tree
from production import generators
from production.models import (
    ModelPart,
    Part,
    PartColoring,
    PartMaterial,
    Variant,
    slot_conflicts,
)
from production.services import interchange_users
from stock.models import (
    ItemKind,
    ProductionRun,
    ProductionRunMaterial,
    Receiving,
    ReceivingLine,
    StockReason,
    Warehouse,
    balance_of,
)

pytestmark = pytest.mark.django_db


def cat(name):
    return MaterialCategory.objects.get(name=name)


def link(first, second):
    cat(first).interchangeable.add(cat(second))


@pytest.fixture
def demo():
    data = load_demo()
    recalculate_all()
    data["wh"] = Warehouse.objects.create(name="Тестовый")
    return data


@pytest.fixture
def red_petg(demo):
    """Красный PETG по 8 ₸/г: категория PETG с PLA пока не связана."""
    material = Material(
        name="PETG", category=cat("PETG"), unit=Unit.GRAM, color=Color.objects.get(name="Красный")
    )
    material.full_clean(exclude=["name_key"])
    material.save()
    MaterialOffer.objects.create(material=material, pack_size=D(1000), pack_price=D(8000))
    return material


def petg_variant(model, material, name="Красный PETG"):
    """Вариант, у которого группа «Плафон» переведена на материал другой категории."""
    variant = Variant.objects.create(model=model, name=name)
    line = variant.slots.get(slot="Плафон")
    line.material = material
    line.full_clean()
    line.save()
    return variant


def test_link_is_mirrored_and_not_transitive():
    pla, petg, abs_ = cat("PLA"), cat("PETG"), cat("ABS / ASA")
    pla.interchangeable.add(petg)
    petg.interchangeable.add(abs_)
    assert set(petg.interchangeable.all()) == {pla, abs_}  # зеркально, и связей несколько
    assert pla.compatible_ids() == {pla.pk, petg.pk}  # по цепочке до ABS не доходит
    assert abs_.compatible_ids() == {abs_.pk, petg.pk}
    petg.interchangeable.remove(pla)
    assert pla.compatible_ids() == {pla.pk}  # убрали с одной стороны — пропала с обеих


def test_variant_takes_material_of_interchangeable_category(demo, red_petg):
    luna = demo["luna"]
    variant = Variant.objects.create(model=luna, name="Красный PETG")
    line = variant.slots.get(slot="Плафон")
    line.material = red_petg
    with pytest.raises(ValidationError, match="подходят только материалы категории"):
        line.full_clean()

    link("PLA", "PETG")
    line.full_clean()
    line.save()
    # расцветки деталей группы — из PETG, и себестоимость считается по его цене
    for name in ("Плафон Луна", "Фиксатор плафона"):
        assert Part.objects.get(name=name).colorings.filter(label="PETG, красный").exists()
    recalculate_all()
    names = [node.name for node in variant_tree(variant).find("material")]
    assert "PETG, красный" in names and "PLA Basic, белый" not in names
    # пересохранение детали не сбрасывает цвет варианта на материал по умолчанию
    Part.objects.get(name="Плафон Луна").save()
    assert variant.slots.get(slot="Плафон").material == red_petg


def test_run_takes_material_of_interchangeable_category(demo, red_petg):
    wh = demo["wh"]
    shade = Part.objects.get(name="Плафон Луна")
    run = ProductionRun.objects.create(warehouse=wh, part=shade, good_qty=1)
    row = ProductionRunMaterial(run=run, position=shade.main_position, material=red_petg)
    with pytest.raises(ValidationError, match="делается из материалов категории"):
        row.full_clean()

    link("PETG", "PLA")  # с какой стороны поставить связь — не важно
    row.full_clean()
    row.save()
    receiving = Receiving.objects.create(
        warehouse=wh, reason=StockReason.objects.get(name="Начальные остатки")
    )
    ReceivingLine.objects.create(document=receiving, material=red_petg, quantity=D(1000))
    receiving.post()
    run.post()
    # списался PETG, а деталь легла на склад отдельной расцветкой, не смешавшись с белым PLA
    assert balance_of(wh.pk, (ItemKind.MATERIAL, red_petg.pk, None)) == D(750)
    made = PartColoring.get_for(shade, {shade.main_position.pk: red_petg})
    assert made.label == "PETG, красный"
    assert balance_of(wh.pk, (ItemKind.PART, shade.pk, made.pk)) == D(1)


def test_color_group_of_interchangeable_parts(demo, red_petg):
    luna = demo["luna"]
    shade = Part.objects.get(name="Плафон Луна")  # белый PLA, группа «Плафон»
    cap = Part.objects.create(
        name="Крышка плафона", equipment=shade.equipment, batch_minutes=D(60), batch_size=1
    )
    PartMaterial.objects.create(part=cap, material=red_petg, batch_qty=D(20), slot="Плафон")
    problems = slot_conflicts([shade, cap])
    assert len(problems) == 1 and "взаимозаменяемыми" in problems[0]

    link("PLA", "PETG")
    assert slot_conflicts([shade, cap]) == []
    ModelPart.objects.create(model=luna, part=cap, quantity=1)
    pla, petg, abs_ = cat("PLA"), cat("PETG"), cat("ABS / ASA")
    assert luna.slot_allowed_categories()["Плафон"] == {pla.pk, petg.pk}
    # ABS связан только с PETG: всей группе он не подходит, потому что не заменяет PLA
    petg.interchangeable.add(abs_)
    assert luna.slot_allowed_categories()["Плафон"] == {pla.pk, petg.pk}
    # связь держат и модель, и три её варианта: они красят одним PLA и крышку из PETG
    users = interchange_users(pla.pk, petg.pk)
    assert "модель «Светильник Луна» (группа «Плафон»)" in users
    assert sum(user.startswith("вариант «Светильник Луна") for user in users) == 3
    assert len(users) == 4
    assert interchange_users(petg.pk, abs_.pk) == []


def test_generator_takes_interchangeable_materials(demo, red_petg):
    luna = demo["luna"]
    with pytest.raises(ValidationError, match="Не подходят по категории"):
        generators.create_material_variants(luna, [red_petg])
    link("PLA", "PETG")
    variants, products, _ = generators.create_material_variants(luna, [red_petg])
    assert [v.name for v in variants] == ["Красный"] and len(products) == 1
    assert variants[0].slots.get(slot="Плафон").material == red_petg


def test_category_form_links_both_sides_and_protects_used_link(client, admin_user, demo, red_petg):
    client.force_login(admin_user)
    luna = demo["luna"]
    pla, petg = cat("PLA"), cat("PETG")
    url = reverse("admin:catalog_materialcategory_change", args=[pla.pk])

    def save(linked):
        return client.post(
            url,
            {
                "name": pla.name,
                "parent": pla.parent_id,
                "kind": pla.kind,
                "interchangeable": linked,
                "is_active": "on",
                "notes": "",
            },
        )

    assert "Взаимозаменяема с" in client.get(url).content.decode()
    assert save([petg.pk]).status_code == 302
    assert list(petg.interchangeable.all()) == [pla]  # у второй категории связь появилась сама
    listing = client.get(reverse("admin:catalog_materialcategory_changelist")).content.decode()
    assert "Взаимозаменяема с" in listing

    # в карточке варианта теперь предлагается и PETG
    variant = petg_variant(luna, red_petg)
    page = client.get(reverse("admin:production_variant_change", args=[variant.pk]))
    assert "PETG, красный</option>" in page.content.decode()

    # пока вариант на PETG, связь убрать нельзя
    response = save([])
    assert response.status_code == 200
    errors = response.context["adminform"].form.errors["interchangeable"]
    assert "убрать нельзя" in errors[0] and "Красный PETG" in errors[0]
    assert pla.interchangeable.exists()
    assert interchange_users(pla.pk, petg.pk) == [f"вариант «{variant}»"]

    # вернули вариант на PLA — связь снимается с обеих сторон
    variant.slots.update(material=Material.objects.get(name="PLA Basic", color__name="Белый"))
    assert save([]).status_code == 302
    assert not pla.interchangeable.exists() and not petg.interchangeable.exists()


def test_category_form_rejects_another_kind(client, admin_user):
    client.force_login(admin_user)
    plastics = cat("PLA").parent
    response = client.post(
        reverse("admin:catalog_materialcategory_add"),
        {
            "name": "PLA-CF",
            "parent": plastics.pk,
            "kind": "",
            "interchangeable": [cat("PETG").pk, cat("Фанера").pk],
            "is_active": "on",
            "notes": "",
        },
    )
    assert response.status_code == 200
    errors = response.context["adminform"].form.errors["interchangeable"]
    assert "одного вида материала" in errors[0] and "Фанера" in errors[0]
    assert "PETG" not in errors[0]
    assert not MaterialCategory.objects.filter(name="PLA-CF").exists()
