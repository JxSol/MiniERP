"""
Демонстрационные данные — примеры из ТЗ: светильник «Луна» (п. 4.9)
и ёлочные шары с наборами (п. 3.2). Используются командой load_demo и тестами.
"""

from decimal import Decimal as D

from django.db import transaction

from catalog.models import (
    Color,
    Equipment,
    Material,
    MaterialCategory,
    MaterialColor,
    OperationType,
    Technology,
    Unit,
)
from core.models import SiteSettings
from production import generators
from production.models import (
    ModelComponent,
    ModelOperation,
    ModelPart,
    Part,
    PartOperation,
    ProductCategory,
    ProductModel,
)


def _category(name: str) -> MaterialCategory:
    return MaterialCategory.objects.get(name=name)


def _op(name: str) -> OperationType:
    return OperationType.objects.get(name=name)


def _material(name, category, unit, pack_size, pack_price, colors=()):
    material = Material.objects.create(
        name=name,
        category=_category(category),
        unit=unit,
        pack_size=D(pack_size),
        pack_price=D(pack_price),
    )
    for code in colors:
        MaterialColor.objects.create(material=material, color=Color.objects.get(code=code))
    return material


@transaction.atomic
def load_demo() -> dict:
    settings = SiteSettings.load()
    settings.electricity_tariff = D(30)
    settings.labor_rate = D(2000)
    settings.default_scrap_percent = D(5)
    settings.overhead_percent = D(10)
    settings.save()

    pla = _material("PLA Basic", "PLA", Unit.GRAM, 1000, 9000, ["WH", "BK", "RD", "GN", "VT"])
    silk = _material("Silk PLA", "PLA", Unit.GRAM, 1000, 12000, ["GD", "SV"])
    plywood = _material("Фанера 4 мм", "Фанера", Unit.SHEET, 1, 2400)
    cord = _material("Шнур с выключателем", "Электрика", Unit.PIECE, 10, 15000)
    socket = _material("Патрон E27", "Электрика", Unit.PIECE, 1, 400)
    screw = _material("Винт M3", "Крепёж", Unit.PIECE, 100, 1500)
    box = _material("Коробка 20×20×25", "Фирменная", Unit.PIECE, 1, 300)
    _material("Коробочка 8×8×8", "Фирменная", Unit.PIECE, 1, 120)
    big_box = _material("Коробка 40×30×30", "Фирменная", Unit.PIECE, 1, 900)

    printer = Equipment.objects.create(
        name="Bambu P1S",
        technology=Technology.FDM,
        quantity=1,
        purchase_price=D(450_000),
        maintenance_cost=D(100_000),
        resource_hours=D(5_000),
        power_kw=D("0.15"),
    )
    laser = Equipment.objects.create(
        name="Лазер CO₂ 60 Вт",
        technology=Technology.LASER_CO2,
        quantity=1,
        purchase_price=D(1_200_000),
        maintenance_cost=D(300_000),
        resource_hours=D(6_000),
        power_kw=D("1.2"),
        scrap_percent=D(2),
    )

    shade = Part.objects.create(
        name="Плафон Луна",
        code="LUNA-SHADE",
        equipment=printer,
        material=pla,
        batch_material_qty=D(250),
        batch_minutes=D(480),
        batch_size=1,
    )
    PartOperation.objects.create(part=shade, operation_type=_op("Снятие поддержек"), minutes=D(5))
    base = Part.objects.create(
        name="Подставка Луна",
        code="LUNA-BASE",
        equipment=laser,
        material=plywood,
        batch_material_qty=D(1),
        batch_minutes=D(12),
        batch_size=4,
    )
    PartOperation.objects.create(part=base, operation_type=_op("Шлифовка"), minutes=D(2))
    fixator = Part.objects.create(
        name="Фиксатор плафона",
        code="LUNA-FIX",
        equipment=printer,
        material=pla,
        batch_material_qty=D(50),
        batch_minutes=D(120),
        batch_size=10,
    )

    lamps = ProductCategory.objects.create(name="Светильники")
    luna = ProductModel.objects.create(
        name="Светильник Луна",
        code="LUNA",
        category=lamps,
        default_packaging=box,
        default_packing_minutes=D(5),
    )
    ModelPart.objects.create(model=luna, part=shade, quantity=1, slot="Плафон")
    ModelPart.objects.create(model=luna, part=base, quantity=1)
    ModelPart.objects.create(model=luna, part=fixator, quantity=2, slot="Плафон")
    ModelComponent.objects.create(model=luna, material=cord, quantity=D(1))
    ModelComponent.objects.create(model=luna, material=socket, quantity=D(1))
    # винты в демо считаются по 15 ₸ за штуку (упаковка 100 шт за 1 500 ₸)
    ModelComponent.objects.create(model=luna, material=screw, quantity=D(4))
    ModelOperation.objects.create(model=luna, operation_type=_op("Сборка"), minutes=D(15))

    generators.create_color_variants(luna, pla, list(Color.objects.filter(code__in=["WH", "BK"])))
    generators.create_color_variants(luna, silk, list(Color.objects.filter(code="GD")))

    # Ёлочный шар: поштучно и наборами
    ball_part = Part.objects.create(
        name="Корпус шара 6 см",
        code="BALL-BODY",
        equipment=printer,
        material=pla,
        batch_material_qty=D(120),
        batch_minutes=D(240),
        batch_size=8,
    )
    newyear = ProductCategory.objects.create(name="Новый год")
    ball = ProductModel.objects.create(
        name="Ёлочный шар",
        code="BALL",
        category=newyear,
        default_packaging=Material.objects.get(name="Коробочка 8×8×8"),
        default_packing_minutes=D(1),
    )
    ModelPart.objects.create(model=ball, part=ball_part, quantity=1, slot="Корпус")
    generators.create_color_variants(
        ball, pla, list(Color.objects.filter(code__in=["RD", "GN", "VT"])), add_random=True
    )
    generators.create_color_variants(ball, silk, list(Color.objects.filter(code="GD")))
    variants = {v.code: v for v in ball.variants.all()}
    generators.create_set(ball, [variants["RD"]], 100, packaging=big_box, packing_minutes=D(20))
    generators.create_set(ball, [variants["MIX"]], 100, packaging=big_box, packing_minutes=D(20))
    generators.create_set(
        ball,
        [variants["RD"], variants["GN"], variants["VT"], variants["GD"]],
        100,
        packaging=big_box,
        packing_minutes=D(20),
    )
    return {"luna": luna, "ball": ball}
