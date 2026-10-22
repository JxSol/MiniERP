"""
Загрузка данных из базы в спецификации движка (costing.engine).

Каталог загружается целиком за несколько запросов и считается в памяти:
данных немного, поэтому так проще и надёжнее, чем отслеживать зависимости.
Здесь же живёт наследование значений (ТЗ п. 5.2):
  % брака:      деталь → станок → настройки
  ставка труда: операция → тип операции → настройки
"""

from decimal import Decimal

from catalog.models import Equipment, Material, OperationType
from core.models import SiteSettings
from production.models import Coloring, Part, ProductModel, Variant
from sales.models import Product

from .engine import (
    Labor,
    MachineRates,
    MaterialUse,
    PartSpec,
    ProductSpec,
    VariantSpec,
    machine_rates,
)


class CostError(Exception):
    """Себестоимость посчитать нельзя (например, нет цены у материала)."""


class Catalog:
    def __init__(self):
        self.settings = SiteSettings.load()
        self.materials = {
            m.pk: m
            for m in Material.objects.select_related("category", "color").prefetch_related("offers")
        }
        # Средняя цена линейки («PLA Basic» всех цветов) — для вариантов «Ассорти»
        self._line_prices: dict[tuple[int, str], Decimal | None] = {}
        self.equipment = {e.pk: e for e in Equipment.objects.all()}
        self.operation_types = {o.pk: o for o in OperationType.objects.all()}
        self.parts = {p.pk: p for p in Part.objects.prefetch_related("operations")}
        self.models = {
            m.pk: m
            for m in ProductModel.objects.prefetch_related(
                "model_parts", "components", "operations"
            )
        }
        self.variants = {v.pk: v for v in Variant.objects.prefetch_related("slots")}
        self._machines: dict[int, MachineRates] = {}

    # --- Наследование значений ---

    def rate(self, operation_type_id: int, override: Decimal | None) -> Decimal:
        if override is not None:
            return override
        op_type = self.operation_types[operation_type_id]
        return op_type.rate if op_type.rate is not None else self.settings.labor_rate

    def scrap_percent(self, part: Part) -> Decimal:
        if part.scrap_percent is not None:
            return part.scrap_percent
        equipment = self.equipment[part.equipment_id]
        if equipment.scrap_percent is not None:
            return equipment.scrap_percent
        return self.settings.default_scrap_percent

    # --- Строительные блоки ---

    def machine(self, equipment_id: int) -> MachineRates:
        if equipment_id not in self._machines:
            e = self.equipment[equipment_id]
            self._machines[equipment_id] = machine_rates(
                e.name,
                purchase_price=e.purchase_price,
                maintenance_cost=e.maintenance_cost,
                residual_value=e.residual_value,
                resource_hours=e.resource_hours,
                power_kw=e.power_kw,
                electricity_tariff=self.settings.electricity_tariff,
            )
        return self._machines[equipment_id]

    def line_price(self, material: Material) -> Decimal | None:
        key = (material.category_id, material.line_key)
        if key not in self._line_prices:
            prices = [
                m.unit_price
                for m in self.materials.values()
                if m.category_id == material.category_id
                and m.line_key == material.line_key
                and m.is_active
                and m.unit_price is not None
            ]
            self._line_prices[key] = sum(prices, Decimal(0)) / len(prices) if prices else None
        return self._line_prices[key]

    def material_use(
        self, material_id: int, quantity: Decimal, *, any_color: bool = False
    ) -> MaterialUse:
        m = self.materials[material_id]
        if any_color:
            name, price = f"{m.name}, любой цвет", self.line_price(m)
        else:
            name, price = m.full_name, m.unit_price
        if price is None or price <= 0:
            raise CostError(f"У материала «{name}» нет актуальных предложений с ценой больше нуля.")
        return MaterialUse(
            name=name, quantity=quantity, unit=m.get_unit_display(), unit_price=price
        )

    def labor(self, line) -> Labor:
        return Labor(
            name=self.operation_types[line.operation_type_id].name,
            minutes=line.minutes,
            rate=self.rate(line.operation_type_id, line.rate),
        )

    # --- Спецификации ---

    def part_spec(
        self, part: Part, material_id: int | None = None, *, any_color: bool = False
    ) -> PartSpec:
        return PartSpec(
            name=part.name,
            material=self.material_use(
                material_id or part.material_id, part.batch_material_qty, any_color=any_color
            ),
            machine=self.machine(part.equipment_id),
            batch_minutes=part.batch_minutes,
            batch_size=part.batch_size,
            scrap_percent=self.scrap_percent(part),
            operations=tuple(self.labor(op) for op in part.operations.all()),
        )

    def variant_spec(self, model: ProductModel, variant: Variant | None = None) -> VariantSpec:
        """Без варианта — модель с материалами деталей по умолчанию."""
        slot_materials = {s.slot: s.material_id for s in variant.slots.all()} if variant else {}
        random = bool(variant and variant.coloring == Coloring.RANDOM)
        parts = tuple(
            (
                self.part_spec(
                    self.parts[line.part_id],
                    slot_materials.get(line.slot),
                    any_color=random and bool(line.slot),
                ),
                line.quantity,
            )
            for line in model.model_parts.all()
        )
        return VariantSpec(
            name=f"{model.name} — {variant.name}" if variant else model.name,
            parts=parts,
            components=tuple(
                self.material_use(c.material_id, c.quantity) for c in model.components.all()
            ),
            operations=tuple(self.labor(op) for op in model.operations.all()),
        )

    def product_spec(self, product: Product) -> ProductSpec:
        items = []
        for item in product.items.all():
            variant = self.variants[item.variant_id]
            items.append((self.variant_spec(self.models[variant.model_id], variant), item.quantity))
        return ProductSpec(
            name=product.name,
            items=tuple(items),
            packaging=tuple(
                self.material_use(p.material_id, p.quantity) for p in product.packaging.all()
            ),
            operations=tuple(self.labor(op) for op in product.operations.all()),
            overhead_percent=self.settings.overhead_percent,
        )
