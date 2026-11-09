"""
Движок расчёта себестоимости.

Чистые функции: на вход — описания (спецификации) деталей, вариантов и товаров,
на выход — дерево CostNode с разбивкой по статьям. Модуль ничего не знает
про базу данных и Django, поэтому его легко тестировать (см. tests/test_engine.py).

Методика описана в ТЗ, раздел 4. Все суммы считаются в Decimal без округления;
округляет только интерфейс при показе.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

ZERO = Decimal(0)
HUNDRED = Decimal(100)
MINUTES_IN_HOUR = Decimal(60)


# --- Узел дерева себестоимости -------------------------------------------------


@dataclass
class CostNode:
    """
    Строка дерева себестоимости.

    amount — итог строки с учётом количества. Если у строки есть quantity,
    то children раскрывают стоимость ОДНОЙ единицы (unit_cost).
    """

    kind: str
    name: str
    amount: Decimal
    quantity: Decimal | None = None
    unit: str = ""
    unit_cost: Decimal | None = None
    note: str = ""
    children: list[CostNode] = field(default_factory=list)

    def find(self, kind: str) -> list[CostNode]:
        """Все узлы указанного вида в поддереве (включая сам узел)."""
        found = [self] if self.kind == kind else []
        for child in self.children:
            found.extend(child.find(kind))
        return found

    def to_dict(self) -> dict:
        data = {
            "kind": self.kind,
            "name": self.name,
            "amount": str(self.amount),
        }
        if self.quantity is not None:
            data["quantity"] = str(self.quantity)
            data["unit"] = self.unit
            data["unit_cost"] = str(self.unit_cost)
        if self.note:
            data["note"] = self.note
        if self.children:
            data["children"] = [c.to_dict() for c in self.children]
        return data


# --- Входные данные ---------------------------------------------------------------


@dataclass(frozen=True)
class MachineRates:
    """Стоимость часа работы станка, ₸/ч (см. machine_rates)."""

    name: str
    amortization: Decimal
    electricity: Decimal

    @property
    def hourly(self) -> Decimal:
        return self.amortization + self.electricity


@dataclass(frozen=True)
class MaterialUse:
    """Расход материала: количество в единицах расхода × цена за единицу."""

    name: str
    quantity: Decimal
    unit: str
    unit_price: Decimal


@dataclass(frozen=True)
class Labor:
    """Ручная операция: минуты на 1 шт и итоговая ставка, ₸/ч."""

    name: str
    minutes: Decimal
    rate: Decimal


@dataclass(frozen=True)
class PartSpec:
    """
    Деталь. Расход материала и время указываются на партию
    (один стол / один лист), как их показывает слайсер.
    """

    name: str
    materials: tuple[
        MaterialUse, ...
    ]  # quantity — расход на партию; у композитной детали несколько
    machine: MachineRates
    batch_minutes: Decimal
    batch_size: int
    scrap_percent: Decimal
    operations: tuple[Labor, ...] = ()


@dataclass(frozen=True)
class VariantSpec:
    """Вариант модели: детали × количество, покупные комплектующие, сборка."""

    name: str
    parts: tuple[tuple[PartSpec, int], ...]
    components: tuple[MaterialUse, ...] = ()
    operations: tuple[Labor, ...] = ()


@dataclass(frozen=True)
class ProductSpec:
    """Товар: варианты × количество, упаковка товара, комплектация, накладные."""

    name: str
    items: tuple[tuple[VariantSpec, int], ...]
    packaging: tuple[MaterialUse, ...] = ()
    operations: tuple[Labor, ...] = ()
    overhead_percent: Decimal = ZERO


# --- Формулы ----------------------------------------------------------------------


def _pct(value: Decimal) -> str:
    """Процент для названий узлов: «5», «2,5» (без зависимости от Django)."""
    return f"{value.normalize():f}".replace(".", ",")


def machine_rates(
    name: str,
    *,
    purchase_price: Decimal,
    maintenance_cost: Decimal,
    residual_value: Decimal,
    resource_hours: Decimal,
    power_kw: Decimal,
    electricity_tariff: Decimal,
) -> MachineRates:
    """Амортизация и электричество за час работы (ТЗ, п. 4.3)."""
    amortization = (purchase_price + maintenance_cost - residual_value) / resource_hours
    return MachineRates(
        name=name,
        amortization=amortization,
        electricity=power_kw * electricity_tariff,
    )


def scrap_markup(base: Decimal, percent: Decimal) -> Decimal:
    """
    Надбавка на брак (ТЗ, п. 4.5): затраты на все попытки делятся на годные штуки,
    поэтому base × p / (100 − p), а не base × p / 100.
    """
    if percent <= 0:
        return ZERO
    return base * percent / (HUNDRED - percent)


def labor_cost(labor: Labor) -> Decimal:
    return labor.minutes / MINUTES_IN_HOUR * labor.rate


def _material_node(use: MaterialUse, kind: str = "material") -> CostNode:
    return CostNode(
        kind=kind,
        name=use.name,
        amount=use.quantity * use.unit_price,
        quantity=use.quantity,
        unit=use.unit,
        unit_cost=use.unit_price,
    )


def _labor_node(labor: Labor) -> CostNode:
    return CostNode(
        kind="labor",
        name=labor.name,
        amount=labor_cost(labor),
        quantity=labor.minutes,
        unit="мин",
        unit_cost=labor.rate / MINUTES_IN_HOUR,
    )


def _scaled(kind: str, name: str, unit_node: CostNode, quantity: int | Decimal) -> CostNode:
    """Строка «N шт × стоимость единицы»; дети раскрывают одну единицу."""
    quantity = Decimal(quantity)
    return CostNode(
        kind=kind,
        name=name,
        amount=unit_node.amount * quantity,
        quantity=quantity,
        unit="шт",
        unit_cost=unit_node.amount,
        children=unit_node.children,
    )


# --- Расчёт уровней ---------------------------------------------------------------


def part_cost(spec: PartSpec) -> CostNode:
    """Себестоимость одной детали (ТЗ, п. 4.1)."""
    batch_size = Decimal(spec.batch_size)

    materials = [
        _material_node(
            MaterialUse(
                name=use.name,
                quantity=use.quantity / batch_size,
                unit=use.unit,
                unit_price=use.unit_price,
            )
        )
        for use in spec.materials
    ]
    material_amount = sum((m.amount for m in materials), ZERO)

    hours = spec.batch_minutes / MINUTES_IN_HOUR / batch_size
    machine = CostNode(
        kind="machine",
        name=spec.machine.name,
        amount=hours * spec.machine.hourly,
        quantity=hours,
        unit="ч",
        unit_cost=spec.machine.hourly,
        children=[
            CostNode(
                kind="amortization", name="Амортизация", amount=hours * spec.machine.amortization
            ),
            CostNode(
                kind="electricity", name="Электричество", amount=hours * spec.machine.electricity
            ),
        ],
    )

    children = [*materials, machine]
    scrap = scrap_markup(material_amount + machine.amount, spec.scrap_percent)
    if scrap:
        children.append(
            CostNode(kind="scrap", name=f"Брак {_pct(spec.scrap_percent)} %", amount=scrap)
        )
    children.extend(_labor_node(op) for op in spec.operations)

    return CostNode(
        kind="part",
        name=spec.name,
        amount=sum((c.amount for c in children), ZERO),
        children=children,
    )


def variant_cost(spec: VariantSpec) -> CostNode:
    """Себестоимость одной штуки варианта: детали + покупное + сборка."""
    children = [_scaled("part", part.name, part_cost(part), qty) for part, qty in spec.parts]
    children.extend(_material_node(c, kind="component") for c in spec.components)
    children.extend(_labor_node(op) for op in spec.operations)
    return CostNode(
        kind="variant",
        name=spec.name,
        amount=sum((c.amount for c in children), ZERO),
        children=children,
    )


def product_cost(spec: ProductSpec) -> CostNode:
    """
    Полная себестоимость товара. Дети верхнего уровня: варианты, упаковка,
    комплектация и последним — накладные. Прямую себестоимость отдаёт direct_cost().
    """
    children = [
        _scaled("variant", variant.name, variant_cost(variant), qty) for variant, qty in spec.items
    ]
    children.extend(_material_node(p, kind="packaging") for p in spec.packaging)
    children.extend(_labor_node(op) for op in spec.operations)

    direct = sum((c.amount for c in children), ZERO)
    overhead = direct * spec.overhead_percent / HUNDRED
    if overhead:
        children.append(
            CostNode(
                kind="overhead",
                name=f"Накладные {_pct(spec.overhead_percent)} %",
                amount=overhead,
            )
        )

    return CostNode(
        kind="product",
        name=spec.name,
        amount=direct + overhead,
        children=children,
    )


def direct_cost(product: CostNode) -> Decimal:
    """Прямая себестоимость товара — итог без накладных."""
    return product.amount - sum((c.amount for c in product.children if c.kind == "overhead"), ZERO)
