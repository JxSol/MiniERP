"""Отчётные функции склада: фактический брак, остатки по всем складам, что заканчивается."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from catalog.models import Material

from .models import DocumentStatus, ProductionRun, StockBalance, Warehouse


def has_showcase() -> bool:
    """Есть ли хотя бы один действующий склад-витрина (тогда в остатках своя колонка)."""
    return Warehouse.objects.filter(is_showcase=True, is_active=True).exists()


def totals_by_item(*, showcase: bool | None = None) -> dict[tuple, Decimal]:
    """
    {ключ позиции: остаток по складам} одним запросом.
    showcase=False — только обычные склады (доступно), True — только витрины, None — все.
    """
    qs = StockBalance.objects.all()
    if showcase is not None:
        qs = qs.filter(warehouse__is_showcase=showcase)
    rows = qs.values(
        "material_id", "part_id", "part_coloring_id", "variant_id", "product_id"
    ).annotate(total=Sum("quantity"))
    result = {}
    for r in rows:
        if r["material_id"]:
            key = ("material", r["material_id"], None)
        elif r["part_id"]:
            key = ("part", r["part_id"], r["part_coloring_id"])
        elif r["variant_id"]:
            key = ("variant", r["variant_id"], None)
        else:
            key = ("product", r["product_id"], None)
        result[key] = r["total"]
    return result


def fact_scrap_percent(*, part=None, equipment=None) -> Decimal | None:
    """Фактический % брака по проведённым выпускам: брак / (годные + брак)."""
    qs = ProductionRun.objects.filter(status=DocumentStatus.POSTED)
    if part is not None:
        qs = qs.filter(part=part)
    if equipment is not None:
        qs = qs.filter(equipment=equipment)
    totals = qs.aggregate(good=Sum("good_qty"), scrap=Sum("scrap_qty"))
    good, scrap = totals["good"] or 0, totals["scrap"] or 0
    if good + scrap == 0:
        return None
    return Decimal(scrap) * 100 / Decimal(good + scrap)


def material_totals() -> dict[int, Decimal]:
    """{material_id: остаток по всем складам}."""
    rows = (
        StockBalance.objects.filter(material__isnull=False)
        .values("material_id")
        .annotate(total=Sum("quantity"))
    )
    return {r["material_id"]: r["total"] for r in rows}


def low_stock_materials() -> list[tuple[Material, Decimal]]:
    """Материалы, у которых остаток по всем складам ниже минимального."""
    totals = material_totals()
    result = []
    for material in Material.objects.filter(is_active=True, min_stock__gt=0).select_related(
        "color"
    ):
        total = totals.get(material.pk, Decimal(0))
        if total < material.min_stock:
            result.append((material, total))
    return result
