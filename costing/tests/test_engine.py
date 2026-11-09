"""
Сквозной пример из ТЗ, п. 4.9: светильник «Луна белая».
Итоги должны совпадать с ТЗ до копейки.
"""

from decimal import Decimal as D

import pytest

from costing.engine import (
    Labor,
    MaterialUse,
    PartSpec,
    ProductSpec,
    VariantSpec,
    direct_cost,
    machine_rates,
    part_cost,
    product_cost,
    scrap_markup,
    variant_cost,
)

TARIFF = D(30)
LABOR_RATE = D(2000)

PRINTER = machine_rates(
    "Bambu P1S",
    purchase_price=D(450_000),
    maintenance_cost=D(100_000),
    residual_value=D(0),
    resource_hours=D(5_000),
    power_kw=D("0.15"),
    electricity_tariff=TARIFF,
)
LASER = machine_rates(
    "Лазер CO₂",
    purchase_price=D(1_200_000),
    maintenance_cost=D(300_000),
    residual_value=D(0),
    resource_hours=D(6_000),
    power_kw=D("1.2"),
    electricity_tariff=TARIFF,
)


def money(value: D) -> D:
    return value.quantize(D("0.01"))


def shade(pla_price: D) -> PartSpec:
    return PartSpec(
        name="Плафон",
        materials=(MaterialUse("PLA", D(250), "г", pla_price),),
        machine=PRINTER,
        batch_minutes=D(480),
        batch_size=1,
        scrap_percent=D(5),
        operations=(Labor("Снятие поддержек", D(5), LABOR_RATE),),
    )


BASE = PartSpec(
    name="Подставка",
    materials=(MaterialUse("Фанера 4 мм", D(1), "лист", D(2400)),),
    machine=LASER,
    batch_minutes=D(12),
    batch_size=4,
    scrap_percent=D(2),
    operations=(Labor("Шлифовка", D(2), LABOR_RATE),),
)


def fixator(pla_price: D) -> PartSpec:
    return PartSpec(
        name="Фиксатор",
        materials=(MaterialUse("PLA", D(50), "г", pla_price),),
        machine=PRINTER,
        batch_minutes=D(120),
        batch_size=10,
        scrap_percent=D(5),
    )


def luna(pla_price: D) -> VariantSpec:
    return VariantSpec(
        name="Луна",
        parts=((shade(pla_price), 1), (BASE, 1), (fixator(pla_price), 2)),
        components=(
            MaterialUse("Шнур с выключателем", D(1), "шт", D(1500)),
            MaterialUse("Патрон E27", D(1), "шт", D(400)),
            MaterialUse("Винт M3", D(4), "шт", D(15)),
        ),
        operations=(Labor("Сборка", D(15), LABOR_RATE),),
    )


def test_machine_rates():
    assert PRINTER.amortization == D(110)
    assert PRINTER.electricity == D("4.5")
    assert PRINTER.hourly == D("114.5")
    assert LASER.hourly == D(286)


def test_scrap_markup_divides_by_good_pieces():
    # 10 % брака → годная штука дороже на 11,1 %, а не на 10 %
    assert money(scrap_markup(D(100), D(10))) == D("11.11")
    assert scrap_markup(D(100), D(0)) == 0


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        (shade(D(9)), D("3499.30")),
        (BASE, D("693.50")),
        (fixator(D(9)), D("71.47")),
        (shade(D(12)), D("4288.77")),
        (fixator(D(12)), D("87.26")),
    ],
)
def test_parts(spec, expected):
    assert money(part_cost(spec).amount) == expected


def test_variant_white_and_gold():
    assert money(variant_cost(luna(D(9))).amount) == D("6795.75")
    assert money(variant_cost(luna(D(12))).amount) == D("7616.80")


def test_product_luna_white():
    product = product_cost(
        ProductSpec(
            name="Луна белая",
            items=((luna(D(9)), 1),),
            packaging=(MaterialUse("Коробка", D(1), "шт", D(300)),),
            operations=(Labor("Упаковка", D(5), LABOR_RATE),),
            overhead_percent=D(10),
        )
    )
    assert money(direct_cost(product)) == D("7262.42")
    assert money(product.find("overhead")[0].amount) == D("726.24")
    assert money(product.amount) == D("7988.66")


def test_set_of_variants_sums_items():
    one = variant_cost(luna(D(9))).amount
    product = product_cost(ProductSpec(name="Набор", items=((luna(D(9)), 3),)))
    assert product.amount == one * 3
    assert product.children[0].unit_cost == one


def test_tree_serializes():
    data = variant_cost(luna(D(9))).to_dict()
    assert data["kind"] == "variant"
    assert data["children"][0]["name"] == "Плафон"
