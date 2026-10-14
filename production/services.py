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
