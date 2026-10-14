"""
Генераторы (ТЗ п. 5.1): варианты по цветам, наборы, размерные модели,
поштучные товары, копирование детали. Вводить минимум — остальное создаётся само.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from catalog.models import Color, Material, OperationType
from core.models import Size
from sales.models import Product, ProductItem, ProductKind, ProductOperation, ProductPackaging

from .models import (
    Coloring,
    ModelComponent,
    ModelOperation,
    ModelPart,
    Part,
    PartOperation,
    ProductModel,
    Variant,
)

RANDOM_CODE = "MIX"
RANDOM_NAME = "Ассорти"


def _operation(key: str) -> OperationType | None:
    return OperationType.objects.filter(key=key, is_active=True).first()


def slot_category_ids(model: ProductModel) -> set[int]:
    return {m.category_id for m in model.slot_default_materials().values()}


# --- Поштучный товар ---------------------------------------------------------------


def single_product_sku(variant: Variant) -> str:
    return f"{variant.model.code}-{variant.code}"


@transaction.atomic
def create_single_product(variant: Variant) -> Product | None:
    """Поштучный товар «вариант × 1» с упаковкой модели. None — если такой уже есть."""
    model = variant.model
    sku = single_product_sku(variant)
    name = f"{model.name} — {variant.name}"
    if (
        Product.objects.filter(sku=sku).exists()
        or Product.objects.filter(name__iexact=name).exists()
    ):
        return None
    product = Product.objects.create(sku=sku, name=name, kind=ProductKind.SINGLE)
    ProductItem.objects.create(product=product, variant=variant, quantity=1)
    if model.default_packaging_id:
        ProductPackaging.objects.create(
            product=product, material=model.default_packaging, quantity=Decimal(1)
        )
        pack = _operation("pack")
        minutes = model.default_packing_minutes or (pack.default_minutes if pack else None)
        if pack and minutes:
            ProductOperation.objects.create(product=product, operation_type=pack, minutes=minutes)
    return product


# --- Варианты по цветам ------------------------------------------------------------


@transaction.atomic
def create_color_variants(
    model: ProductModel,
    material: Material,
    colors: list[Color],
    *,
    add_random: bool = False,
    create_products: bool = True,
) -> tuple[list[Variant], list[Product]]:
    """
    Одноцветные варианты: во все слоты модели — выбранный материал и цвет.
    Варианты с уже существующим кодом пропускаются.
    """
    categories = slot_category_ids(model)
    if not categories:
        raise ValidationError("У модели нет цветовых слотов — укажите слот хотя бы у одной детали.")
    if categories != {material.category_id}:
        raise ValidationError(
            "Генератор работает, когда все слоты модели из одной категории материалов. "
            "Многоцветные варианты создайте вручную."
        )

    existing = set(model.variants.values_list("code", flat=True))
    variants: list[Variant] = []

    def make(name: str, code: str, coloring: str, color: Color | None):
        if code in existing:
            return
        variant = Variant.objects.create(model=model, name=name, code=code, coloring=coloring)
        variant.slots.update(material=material, color=color)
        variants.append(variant)

    for color in colors:
        make(color.name, color.code, Coloring.FIXED, color)
    if add_random:
        make(RANDOM_NAME, RANDOM_CODE, Coloring.RANDOM, None)

    products = []
    if create_products:
        for variant in variants:
            product = create_single_product(variant)
            if product:
                products.append(product)
    return variants, products


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
        variant = variants[0]
        sku = f"{model.code}-{variant.code}-{total}"
        name = f"{model.name} — {variant.name}, {total} шт"
    else:
        sku = f"{model.code}-MIX{count}-{total}"
        name = f"{model.name} — микс {count} цв., {total} шт"

    if Product.objects.filter(sku=sku).exists():
        raise ValidationError(f"Товар с артикулом {sku} уже есть.")

    product = Product.objects.create(sku=sku, name=name, kind=ProductKind.SET)
    for variant in variants:
        ProductItem.objects.create(product=product, variant=variant, quantity=total // count)
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
        code=f"{base.code}-{size.code}",
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
        ModelPart.objects.create(model=copy, part=line.part, quantity=line.quantity, slot=line.slot)
    for line in base.components.all():
        ModelComponent.objects.create(model=copy, material=line.material, quantity=line.quantity)
    for line in base.operations.all():
        ModelOperation.objects.create(
            model=copy, operation_type=line.operation_type, minutes=line.minutes, rate=line.rate
        )
    for variant in base.variants.filter(is_active=True):
        new_variant = Variant.objects.create(
            model=copy,
            name=variant.name,
            code=variant.code,
            coloring=variant.coloring,
            card_warning=variant.card_warning,
        )
        for slot in variant.slots.all():
            new_variant.slots.filter(slot=slot.slot).update(
                material=slot.material, color=slot.color
            )
        create_single_product(new_variant)
    return copy


# --- Копия детали ------------------------------------------------------------------


@transaction.atomic
def copy_part(part: Part) -> Part:
    n = 2
    while (
        Part.objects.filter(code=f"{part.code}-{n}").exists()
        or Part.objects.filter(name__iexact=f"{part.name} (копия {n})").exists()
    ):
        n += 1
    copy = Part.objects.create(
        name=f"{part.name} (копия {n})",
        code=f"{part.code}-{n}",
        equipment=part.equipment,
        material=part.material,
        batch_material_qty=part.batch_material_qty,
        batch_minutes=part.batch_minutes,
        batch_size=part.batch_size,
        scrap_percent=part.scrap_percent,
        model_url=part.model_url,
        print_params=part.print_params,
    )
    for op in part.operations.all():
        PartOperation.objects.create(
            part=copy, operation_type=op.operation_type, minutes=op.minutes, rate=op.rate
        )
    return copy
