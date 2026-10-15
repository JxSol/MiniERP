"""Служебные операции производства: синхронизация слотов вариантов."""

from .models import ProductModel, Variant, VariantSlot


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
            line.color = None
            line.save()
    for slot, line in existing.items():
        if slot not in defaults:
            line.delete()


def sync_model_slots(model: ProductModel) -> None:
    for variant in model.variants.all():
        sync_variant_slots(variant)


def snapshot_slots(model: ProductModel) -> dict[int, dict[str, tuple[int, int | None]]]:
    """{variant_id: {slot: (material_id, color_id)}} — чтобы пережить переименование слота."""
    result: dict[int, dict[str, tuple[int, int | None]]] = {}
    for line in VariantSlot.objects.filter(variant__model=model):
        result.setdefault(line.variant_id, {})[line.slot] = (line.material_id, line.color_id)
    return result


def restore_renamed_slots(model: ProductModel, before: dict, renames: dict[str, str]) -> None:
    """
    После переименования слота (старое → новое) строки вариантов уже пересозданы
    с материалом по умолчанию и без цвета. Возвращаем материал и цвет из снимка,
    если категория материала не поменялась.
    """
    if not renames:
        return
    defaults = model.slot_default_materials()
    for variant in model.variants.all():
        old_rows = before.get(variant.pk, {})
        for line in variant.slots.filter(slot__in=list(renames.values()), color__isnull=True):
            old_name = next((o for o, n in renames.items() if n == line.slot), None)
            if old_name is None or old_name not in old_rows:
                continue
            material_id, color_id = old_rows[old_name]
            default = defaults.get(line.slot)
            old_material = line.material.__class__.objects.filter(pk=material_id).first()
            if default is None or old_material is None:
                continue
            if old_material.category_id != default.category_id:
                continue
            line.material_id = material_id
            line.color_id = color_id
            line.save()
