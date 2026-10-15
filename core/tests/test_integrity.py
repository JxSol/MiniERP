"""Целостность данных: архив, переименование слота, смена материала слота."""

import pytest
from django.urls import reverse

from catalog.models import Color, Material
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
    pla = Material.objects.get(name="PLA Basic")
    pla.is_active = False
    pla.save()
    from production.models import Part

    shade = Part.objects.get(code="LUNA-SHADE")
    client.force_login(admin_user)
    url = reverse("admin:production_part_change", args=[shade.pk])
    data = {
        "name": shade.name,
        "code": shade.code,
        "equipment": shade.equipment_id,
        "material": pla.pk,
        "batch_material_qty": "250",
        "batch_minutes": "480",
        "batch_size": "1",
        "is_active": "on",
        "notes": "изменено",
        "operations-TOTAL_FORMS": "0",
        "operations-INITIAL_FORMS": "0",
        "photos-TOTAL_FORMS": "0",
        "photos-INITIAL_FORMS": "0",
    }
    response = client.post(url, data)
    assert response.status_code == 302, response.content.decode()[:500]
    shade.refresh_from_db()
    assert shade.notes == "изменено" and shade.material_id == pla.pk
    # а в автокомплите архивный материал уже не предлагается
    search = client.get(
        reverse("admin:autocomplete"),
        {"app_label": "production", "model_name": "part", "field_name": "material", "term": "PLA"},
    ).json()
    assert all(r["text"] != "PLA Basic" for r in search["results"])


def test_slot_rename_keeps_variant_colors(demo):
    luna = ProductModel.objects.get(code="LUNA")
    gold = Variant.objects.get(model=luna, code="GD")
    before = snapshot_slots(luna)
    luna.model_parts.filter(slot="Плафон").update(slot="Корпус")
    from production.services import sync_model_slots

    sync_model_slots(luna)
    gold.refresh_from_db()
    assert gold.slots.get(slot="Корпус").color is None  # без переноса цвет потерян
    restore_renamed_slots(luna, before, {"Плафон": "Корпус"})
    line = gold.slots.get(slot="Корпус")
    assert line.color.code == "GD" and line.material.name == "Silk PLA"


def test_slot_material_can_be_changed_with_new_color(demo):
    from production.admin import VariantSlotForm

    gold = Variant.objects.get(model__code="LUNA", code="GD")
    line = gold.slots.get(slot="Плафон")
    pla = Material.objects.get(name="PLA Basic")
    white = Color.objects.get(code="WH")
    form = VariantSlotForm({"material": pla.pk, "color": white.pk}, instance=line)
    assert form.is_valid(), form.errors
    form.save()
    line.refresh_from_db()
    assert line.material == pla and line.color == white
