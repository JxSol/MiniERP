"""
Генераторы (ТЗ п. 5.1): варианты по материалам, наборы, размерные модели,
поштучные товары, копирование детали. Коды и артикулы всегда создаются системой.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from catalog.models import Equipment, Material, OperationType
from core.models import Size, name_key
from sales.models import Product, ProductItem, ProductKind, ProductOperation, ProductPackaging

from .models import (
    Coloring,
    ModelComponent,
    ModelOperation,
    ModelPart,
    Part,
    PartMaterial,
    PartOperation,
    ProductModel,
    Variant,
)

RANDOM_NAME = "Ассорти"


def _operation(key: str) -> OperationType | None:
    return OperationType.objects.filter(key=key, is_active=True).first()


def common_category_ids(model: ProductModel) -> set[int]:
    """Категории материалов, которые подходят сразу всем цветовым группам модели."""
    allowed = list(model.slot_allowed_categories().values())
    return set.intersection(*allowed) if allowed else set()


# --- Поштучный товар ---------------------------------------------------------------


def single_product_name(variant: Variant) -> str:
    return f"{variant.model.name} — {variant.name}"


@transaction.atomic
def create_single_product(variant: Variant) -> Product | None:
    """Поштучный товар «вариант × 1» с упаковкой модели. None — если такой уже есть."""
    model = variant.model
    name = single_product_name(variant)
    if Product.objects.filter(name_key=name_key(name)).exists():
        return None
    if ProductItem.objects.filter(variant=variant, product__kind=ProductKind.SINGLE).exists():
        return None
    product = Product.objects.create(name=name, kind=ProductKind.SINGLE)
    ProductItem.objects.create(product=product, variant=variant, quantity=1)
    product.assign_article()
    if model.default_packaging_id:
        ProductPackaging.objects.create(
            product=product, material=model.default_packaging, quantity=Decimal(1)
        )
        pack = _operation("pack")
        minutes = model.default_packing_minutes or (pack.default_minutes if pack else None)
        if pack and minutes:
            ProductOperation.objects.create(product=product, operation_type=pack, minutes=minutes)
    return product


def single_product_of(variant: Variant) -> Product | None:
    item = (
        ProductItem.objects.filter(variant=variant, product__kind=ProductKind.SINGLE)
        .select_related("product")
        .first()
    )
    return item.product if item else None


# --- Варианты по материалам --------------------------------------------------------


@transaction.atomic
def create_material_variants(
    model: ProductModel,
    materials: list[Material],
    *,
    add_random: bool = False,
    create_products: bool = True,
) -> tuple[list[Variant], list[Product], list[str]]:
    """
    Одноцветные варианты: во все слоты модели — выбранный материал («PLA Basic, зелёный»).
    Название варианта — цвет материала. Уже существующие названия пропускаются.
    """
    if not model.slot_names():
        raise ValidationError(
            "У модели нет цветовых групп — укажите группу хотя бы у одной позиции детали."
        )
    categories = common_category_ids(model)
    if not categories:
        raise ValidationError(
            "Генератор работает, когда всем цветовым группам модели подходит один и тот же "
            "материал. Многоцветные варианты создайте вручную."
        )
    wrong = [m for m in materials if m.category_id not in categories]
    if wrong:
        raise ValidationError("Не подходят по категории: " + ", ".join(str(m) for m in wrong) + ".")

    existing_names = set(model.variants.values_list("name_key", flat=True))
    variants: list[Variant] = []
    skipped: list[str] = []

    def make(name: str, coloring: str, material: Material):
        if name_key(name) in existing_names:
            skipped.append(name)
            return
        variant = Variant.objects.create(model=model, name=name, coloring=coloring)
        variant.slots.update(material=material)
        variants.append(variant)
        existing_names.add(name_key(name))

    for material in materials:
        name = material.color.name if material.color_id else material.name
        make(name, Coloring.FIXED, material)
    if add_random:
        if not materials:
            raise ValidationError("Для «Ассорти» отметьте хотя бы один материал линейки.")
        make(RANDOM_NAME, Coloring.RANDOM, materials[0])

    products = []
    if create_products:
        for variant in variants:
            product = create_single_product(variant)
            if product:
                products.append(product)
    return variants, products, skipped


# --- Наборы ------------------------------------------------------------------------


@transaction.atomic
def create_set(
    model: ProductModel,
    variants: list[Variant],
    total: int,
    *,
    packaging: Material | None = None,
    packing_minutes: Decimal | None = None,
) -> Product:
    """
    Набор из N штук: один вариант («Шары красные, 100 шт») или ровный микс
    нескольких («Шары микс ровный, 100 шт» = по 25 каждого).
    """
    if not variants:
        raise ValidationError("Выберите хотя бы один вариант.")
    if total < 2:
        raise ValidationError("В наборе должно быть хотя бы 2 штуки.")
    count = len(variants)
    if total % count:
        raise ValidationError(
            f"{total} шт нельзя разделить поровну на {count} вариантов — укажите количество, "
            f"кратное {count}."
        )

    if count == 1:
        name = f"{model.name} — {variants[0].name}, {total} шт"
    else:
        name = f"{model.name} — микс ровный ({count} цв.), {total} шт"
    if Product.objects.filter(name_key=name_key(name)).exists():
        raise ValidationError(f"Товар «{name}» уже есть.")

    product = Product.objects.create(name=name, kind=ProductKind.SET)
    for variant in variants:
        ProductItem.objects.create(product=product, variant=variant, quantity=total // count)
    product.assign_article()
    if packaging:
        ProductPackaging.objects.create(product=product, material=packaging, quantity=Decimal(1))
    kit = _operation("kit")
    minutes = packing_minutes or (kit.default_minutes if kit else None)
    if kit and minutes:
        ProductOperation.objects.create(product=product, operation_type=kit, minutes=minutes)
    return product


# --- Размерные модели --------------------------------------------------------------


@transaction.atomic
def create_size(model: ProductModel, size: Size) -> ProductModel:
    """Копия базовой модели со всем составом и вариантами (ТЗ п. 3.6)."""
    base = model.base_model or model
    if base.sizes.filter(size=size).exists():
        raise ValidationError(f"Размер {size} у модели «{base.name}» уже есть.")

    copy = ProductModel.objects.create(
        name=f"{base.name} {size.name}",
        category=base.category,
        description=base.description,
        default_packaging=base.default_packaging,
        default_packing_minutes=base.default_packing_minutes,
        marketplace_group=base.marketplace_group,
        base_model=base,
        size=size,
    )
    copy.tags.set(base.tags.all())
    for line in base.model_parts.all():
        ModelPart.objects.create(model=copy, part=line.part, quantity=line.quantity)
    for line in base.components.all():
        ModelComponent.objects.create(model=copy, material=line.material, quantity=line.quantity)
    for line in base.operations.all():
        ModelOperation.objects.create(
            model=copy, operation_type=line.operation_type, minutes=line.minutes, rate=line.rate
        )
    for variant in base.variants.filter(is_active=True).order_by("code"):
        new_variant = Variant.objects.create(
            model=copy,
            name=variant.name,
            coloring=variant.coloring,
            card_warning=variant.card_warning,
        )
        for slot in variant.slots.all():
            new_variant.slots.filter(slot=slot.slot).update(material=slot.material)
        create_single_product(new_variant)
    return copy


# --- Копия детали ------------------------------------------------------------------


@transaction.atomic
def copy_part(part: Part, equipment: Equipment | None = None) -> Part:
    """Копия детали; с другим станком — и с другой технологией в коде."""
    n = 2
    while Part.objects.filter(name_key=name_key(f"{part.name} (копия {n})")).exists():
        n += 1
    copy = Part.objects.create(
        name=f"{part.name} (копия {n})",
        equipment=equipment or part.equipment,
        batch_minutes=part.batch_minutes,
        batch_size=part.batch_size,
        scrap_percent=part.scrap_percent,
        model_url=part.model_url,
        print_params=part.print_params,
    )
    for position in part.positions:
        PartMaterial.objects.create(
            part=copy,
            order=position.order,
            name=position.name,
            material=position.material,
            batch_qty=position.batch_qty,
            batch_unit=position.batch_unit,
            slot=position.slot,
        )
    for op in part.operations.all():
        PartOperation.objects.create(
            part=copy, operation_type=op.operation_type, minutes=op.minutes, rate=op.rate
        )
    return copy
