"""Служебные операции производства: синхронизация цветовых групп (слотов) и расцветок деталей."""

from catalog.models import Material

from .models import Coloring, PartColoring, ProductModel, Variant, VariantSlot


def sync_variant_slots(variant: Variant) -> None:
    """
    Строки «цвет по слотам» у варианта всегда соответствуют слотам модели:
    новый слот — новая строка с материалом по умолчанию, удалённый слот — строка удаляется.
    Если материал слота стал несовместимым (сменилась деталь), он сбрасывается на умолчание.
    """
    defaults = variant.model.slot_default_materials()
    existing = {s.slot: s for s in variant.slots.select_related("material")}
    for slot, material in defaults.items():
        line = existing.get(slot)
        if line is None:
            VariantSlot.objects.create(variant=variant, slot=slot, material=material)
        elif line.material.category_id != material.category_id:
            line.material = material
            line.save()
    for slot, line in existing.items():
        if slot not in defaults:
            line.delete()
    ensure_variant_colorings(variant)


def variant_part_materials(variant: Variant) -> dict[int, dict[int, Material]]:
    """{id детали: {id позиции: материал}} — из чего печатать каждую деталь этого варианта."""
    slot_materials = {s.slot: s.material for s in variant.slots.select_related("material")}
    result: dict[int, dict[int, Material]] = {}
    for line in variant.model.model_parts.select_related("part").prefetch_related(
        "part__materials__material"
    ):
        mapping = {}
        for position in line.part.positions:
            material = slot_materials.get(position.slot) if position.slot else None
            mapping[position.pk] = material or position.material
        result[line.part_id] = mapping
    return result


def ensure_variant_colorings(variant: Variant) -> None:
    """Расцветки деталей, которые нужны этому варианту, существуют (для сборки и инвентаризации)."""
    if variant.coloring == Coloring.RANDOM:
        return
    parts = {line.part_id: line.part for line in variant.model.model_parts.select_related("part")}
    for part_id, mapping in variant_part_materials(variant).items():
        PartColoring.get_for(parts[part_id], mapping)


def sync_model_slots(model: ProductModel) -> None:
    for variant in model.variants.all():
        sync_variant_slots(variant)


def snapshot_slots(model: ProductModel) -> dict[int, dict[str, tuple[int, int | None]]]:
    """{variant_id: {slot: material_id}} — чтобы пережить переименование слота."""
    result: dict[int, dict[str, int]] = {}
    for line in VariantSlot.objects.filter(variant__model=model):
        result.setdefault(line.variant_id, {})[line.slot] = line.material_id
    return result


def restore_renamed_slots(model: ProductModel, before: dict, renames: dict[str, str]) -> None:
    """
    После переименования слота (старое → новое) строки вариантов уже пересозданы
    с материалом по умолчанию. Возвращаем материал из снимка, если категория совпадает.
    """
    if not renames:
        return
    defaults = model.slot_default_materials()
    for variant in model.variants.all():
        old_rows = before.get(variant.pk, {})
        for line in variant.slots.filter(slot__in=list(renames.values())):
            old_name = next((o for o, n in renames.items() if n == line.slot), None)
            if old_name is None or old_name not in old_rows:
                continue
            material_id = old_rows[old_name]
            default = defaults.get(line.slot)
            old_material = line.material.__class__.objects.filter(pk=material_id).first()
            if default is None or old_material is None:
                continue
            if old_material.category_id != default.category_id:
                continue
            line.material_id = material_id
            line.save()
