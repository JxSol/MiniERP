"""Служебные операции производства: синхронизация цветовых групп (слотов) и расцветок деталей."""

from django.db.models import F, Q

from catalog.models import Material

from .models import Coloring, PartColoring, PartMaterial, ProductModel, Variant, VariantSlot


def sync_variant_slots(variant: Variant) -> None:
    """
    Строки «цвет по слотам» у варианта всегда соответствуют слотам модели:
    новый слот — новая строка с материалом по умолчанию, удалённый слот — строка удаляется.
    Если материал слота стал несовместимым (сменилась деталь), он сбрасывается на умолчание.
    Совместим — значит из той же категории, что у деталей группы, или из взаимозаменяемой.
    """
    positions = variant.model.slot_positions()
    defaults = variant.model.slot_default_materials(positions)
    allowed = variant.model.slot_allowed_categories(positions)
    existing = {s.slot: s for s in variant.slots.select_related("material")}
    for slot, material in defaults.items():
        line = existing.get(slot)
        if line is None:
            VariantSlot.objects.create(variant=variant, slot=slot, material=material)
        elif line.material.category_id not in allowed[slot]:
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


def interchange_users(first_id: int, second_id: int) -> list[str]:
    """
    Где уже используется взаимозаменяемость двух категорий материалов. Пока список не пуст,
    связь убирать нельзя: цвета таких вариантов молча сбросились бы на материал по умолчанию.

    - варианты: материал группы из одной категории, а материал детали по умолчанию — из другой;
    - модели: детали обеих категорий сведены в одну цветовую группу.
    """
    positions = "variant__model__model_parts__part__materials"
    crossed = Q(
        **{"material__category_id": first_id, f"{positions}__material__category_id": second_id}
    ) | Q(**{"material__category_id": second_id, f"{positions}__material__category_id": first_id})
    # одно условие filter(): группа и категория проверяются у одной и той же позиции детали
    lines = VariantSlot.objects.filter(Q(**{f"{positions}__slot": F("slot")}) & crossed)
    variants = Variant.objects.filter(pk__in=lines.values("variant_id")).select_related("model")
    users = [f"вариант «{variant}»" for variant in variants]

    groups: dict[tuple[int, str], set[int]] = {}
    rows = (
        PartMaterial.objects.filter(material__category_id__in=(first_id, second_id))
        .exclude(slot="")
        .values_list("part__model_lines__model_id", "slot", "material__category_id")
    )
    for model_id, slot, category_id in rows:
        if model_id:
            groups.setdefault((model_id, slot), set()).add(category_id)
    mixed = sorted(key for key, categories in groups.items() if len(categories) == 2)
    names = dict(
        ProductModel.objects.filter(pk__in={pk for pk, _ in mixed}).values_list("pk", "name")
    )
    users += [f"модель «{names[model_id]}» (группа «{slot}»)" for model_id, slot in mixed]
    return users


def snapshot_slots(model: ProductModel) -> dict[int, dict[str, tuple[int, int | None]]]:
    """{variant_id: {slot: material_id}} — чтобы пережить переименование слота."""
    result: dict[int, dict[str, int]] = {}
    for line in VariantSlot.objects.filter(variant__model=model):
        result.setdefault(line.variant_id, {})[line.slot] = line.material_id
    return result


def restore_renamed_slots(model: ProductModel, before: dict, renames: dict[str, str]) -> None:
    """
    После переименования слота (старое → новое) строки вариантов уже пересозданы
    с материалом по умолчанию. Возвращаем материал из снимка, если его категория подходит группе.
    """
    if not renames:
        return
    positions = model.slot_positions()
    defaults = model.slot_default_materials(positions)
    allowed = model.slot_allowed_categories(positions)
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
            if old_material.category_id not in allowed[line.slot]:
                continue
            line.material_id = material_id
            line.save()
