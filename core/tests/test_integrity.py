"""Целостность данных: архив, переименование слота, смена материала слота."""

import pytest
from django.urls import reverse

from catalog.models import Material
from core.demo import load_demo
from costing.services import recalculate_all
from production.models import ProductModel, Variant
from production.services import restore_renamed_slots, snapshot_slots

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo():
    load_demo()
    recalculate_all()


def test_archived_material_keeps_existing_part_valid(client, admin_user, demo):
    """Архив прячет из выбора, но не ломает записи, которые уже ссылаются (ТЗ 5.7)."""
    pla = Material.objects.get(name="PLA Basic", color__name="Белый")
    pla.is_active = False
    pla.save()
    from production.models import Part

    shade = Part.objects.get(name="Плафон Луна")
    position = shade.main_position
    client.force_login(admin_user)
    url = reverse("admin:production_part_change", args=[shade.pk])
    data = {
        "name": shade.name,
        "equipment": shade.equipment_id,
        "batch_minutes_0": "0",
        "batch_minutes_1": "8",
        "batch_minutes_2": "0",
        "batch_minutes_3": "0",
        "batch_size": "1",
        "is_active": "on",
        "notes": "изменено",
        "materials-TOTAL_FORMS": "1",
        "materials-INITIAL_FORMS": "1",
        "materials-MIN_NUM_FORMS": "1",
        "materials-0-id": position.pk,
        "materials-0-name": "",
        "materials-0-material": pla.pk,
        "materials-0-batch_qty": "250",
        "materials-0-batch_unit": "",
        "materials-0-slot": "Плафон",
        "operations-TOTAL_FORMS": "0",
        "operations-INITIAL_FORMS": "0",
        "photos-TOTAL_FORMS": "0",
        "photos-INITIAL_FORMS": "0",
    }
    response = client.post(url, data)
    assert response.status_code == 302, response.content.decode()[:500]
    shade.refresh_from_db()
    assert shade.notes == "изменено" and shade.material.pk == pla.pk
    # а в автокомплите архивный материал уже не предлагается
    search = client.get(
        reverse("admin:autocomplete"),
        {
            "app_label": "production",
            "model_name": "partmaterial",
            "field_name": "material",
            "term": "PLA",
        },
    ).json()
    assert all("белый" not in r["text"] for r in search["results"])


def test_slot_rename_keeps_variant_colors(demo):
    luna = ProductModel.objects.get(name="Светильник Луна")
    gold = Variant.objects.get(model=luna, name="Золотой")
    before = snapshot_slots(luna)
    from production.models import PartMaterial
    from production.services import sync_model_slots

    PartMaterial.objects.filter(part__model_lines__model=luna, slot="Плафон").update(slot="Корпус")

    sync_model_slots(luna)
    gold.refresh_from_db()
    assert gold.slots.get(slot="Корпус").material.name == "PLA Basic"  # без переноса — умолчание
    restore_renamed_slots(luna, before, {"Плафон": "Корпус"})
    assert gold.slots.get(slot="Корпус").material.name == "PLA Silk"


def test_slot_material_can_be_changed_with_new_color(demo):
    from production.admin import VariantSlotForm

    gold = Variant.objects.get(model__name="Светильник Луна", name="Золотой")
    line = gold.slots.get(slot="Плафон")
    pla_white = Material.objects.get(name="PLA Basic", color__name="Белый")
    form = VariantSlotForm({"material": pla_white.pk}, instance=line)
    assert form.is_valid(), form.errors
    form.save()
    line.refresh_from_db()
    assert line.material == pla_white
