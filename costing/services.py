"""
Пересчёт и фиксация себестоимости.

Итоги хранятся в полях cost_cached, чтобы списки открывались быстро и по
себестоимости можно было фильтровать. Пересчёт запускается автоматически
после любого изменения, влияющего на себестоимость (см. costing.signals),
и вручную — командой `python manage.py recalc_costs`.
"""

import logging

from django.db import transaction

from production.models import Part, ProductModel, Variant
from sales.models import CostSnapshot, Product

from .engine import CostNode, direct_cost, part_cost, product_cost, variant_cost
from .loader import Catalog

log = logging.getLogger(__name__)


def _safe(fn, *args):
    """Ошибка в одной записи не должна ломать пересчёт остальных."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001
        log.exception("Не удалось посчитать себестоимость")
        return None


def recalculate_all() -> None:
    catalog = Catalog()

    parts = list(catalog.parts.values())
    for part in parts:
        node = _safe(lambda p: part_cost(catalog.part_spec(p)), part)
        part.cost_cached = node.amount if node else None
    Part.objects.bulk_update(parts, ["cost_cached"])

    models = list(catalog.models.values())
    for model in models:
        node = _safe(lambda m: variant_cost(catalog.variant_spec(m)), model)
        model.cost_cached = node.amount if node else None
    ProductModel.objects.bulk_update(models, ["cost_cached"])

    variants = list(catalog.variants.values())
    for variant in variants:
        node = _safe(
            lambda v: variant_cost(catalog.variant_spec(catalog.models[v.model_id], v)), variant
        )
        variant.cost_cached = node.amount if node else None
    Variant.objects.bulk_update(variants, ["cost_cached"])

    products = list(Product.objects.prefetch_related("items", "packaging", "operations"))
    for product in products:
        node = _safe(lambda p: product_cost(catalog.product_spec(p)), product)
        product.cost_cached = node.amount if node else None
        product.cost_direct_cached = direct_cost(node) if node else None
    Product.objects.bulk_update(products, ["cost_cached", "cost_direct_cached"])


def product_tree(product: Product) -> CostNode:
    return product_cost(Catalog().product_spec(product))


def variant_tree(variant: Variant) -> CostNode:
    return variant_cost(Catalog().variant_spec(variant.model, variant))


def model_tree(model: ProductModel) -> CostNode:
    return variant_cost(Catalog().variant_spec(model))


def part_tree(part: Part) -> CostNode:
    return part_cost(Catalog().part_spec(part))


@transaction.atomic
def fix_cost(product: Product, user=None) -> CostSnapshot:
    """Зафиксировать текущую себестоимость товара (ТЗ п. 4.8)."""
    tree = product_tree(product)
    return CostSnapshot.objects.create(
        product=product,
        created_by=user,
        direct=direct_cost(tree),
        total=tree.amount,
        tree=tree.to_dict(),
    )
