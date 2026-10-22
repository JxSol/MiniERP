"""
Демонстрационные данные — примеры из ТЗ: светильник «Луна» (п. 4.9)
и ёлочные шары с наборами (п. 3.2). Используются командой load_demo и тестами.
"""

from decimal import Decimal as D

from django.db import transaction

from catalog.models import (
    Color,
    Equipment,
    Manufacturer,
    Material,
    MaterialCategory,
    MaterialForm,
    MaterialOffer,
    OperationType,
    Supplier,
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


def _material(
    name,
    category,
    unit,
    pack_size,
    pack_price,
    *,
    color=None,
    maker=None,
    shop=None,
    form_type=MaterialForm.PLAIN,
    width=None,
    length=None,
    height=None,
    roll_length=None,
):
    """Материал с одним актуальным предложением поставщика. Для рулона pack_size=None."""
    material = Material(
        name=name,
        category=_category(category),
        unit=unit,
        color=Color.objects.get(name=color) if color else None,
        form_type=form_type,
        width_cm=D(width) if width else None,
        length_cm=D(length) if length else None,
        height_cm=D(height) if height else None,
        roll_length_m=D(roll_length) if roll_length else None,
    )
    material.full_clean(exclude=["name_key"])
    material.save()
    MaterialOffer.objects.create(
        material=material,
        manufacturer=Manufacturer.objects.filter(name=maker).first() if maker else None,
        supplier=Supplier.objects.filter(name=shop).first() if shop else None,
        pack_size=D(pack_size) if pack_size is not None else material.roll_length_m,
        pack_price=D(pack_price),
    )
    return material


def variant_of(model: ProductModel, name: str):
    return model.variants.get(name=name)


def product_of(model: ProductModel, variant_name: str):
    return generators.single_product_of(variant_of(model, variant_name))


@transaction.atomic
def load_demo() -> dict:
    settings = SiteSettings.load()
    settings.electricity_tariff = D(30)
    settings.labor_rate = D(2000)
    settings.default_scrap_percent = D(5)
    settings.overhead_percent = D(10)
    settings.save()

    def pla(color):
        return _material(
            "PLA Basic", "PLA", Unit.GRAM, 1000, 9000, color=color, maker="Bambu Lab", shop="Kaspi"
        )

    pla_white = pla("Белый")
    pla_black = pla("Чёрный")
    pla_red = pla("Красный")
    pla_green = pla("Зелёный")
    pla_violet = pla("Фиолетовый")
    silk_gold = _material(
        "PLA Silk", "PLA", Unit.GRAM, 1000, 12000, color="Золотой", maker="Bambu Lab", shop="Kaspi"
    )
    plywood = _material(
        "Фанера 4 мм",
        "Фанера",
        Unit.SHEET,
        1,
        2400,
        shop="Местный магазин",
        form_type=MaterialForm.SHEET,
        width=152.5,
        length=152.5,
    )
    cord = _material("Шнур с выключателем", "Электрика", Unit.PIECE, 10, 15000, shop="AliExpress")
    socket = _material("Патрон E27", "Электрика", Unit.PIECE, 1, 400, shop="Местный магазин")
    screw = _material("Винт M3", "Крепёж", Unit.PIECE, 100, 1500, shop="AliExpress")
    piece = {"form_type": MaterialForm.PIECE}
    box = _material(
        "Коробка 20×20×25", "Фирменная", Unit.PIECE, 1, 300, width=20, length=20, height=25, **piece
    )
    small_box = _material(
        "Коробочка 8×8×8", "Фирменная", Unit.PIECE, 1, 120, width=8, length=8, height=8, **piece
    )
    big_box = _material(
        "Коробка 40×30×30", "Фирменная", Unit.PIECE, 1, 900, width=40, length=30, height=30, **piece
    )
    # рулон: 1 рулон = 100 пог. м (1,2 м × 100 м = 120 м²) за 6 000 ₸ → 60 ₸/пог. м, 50 ₸/м²
    _material(
        "ВПП плёнка 120 см",
        "Транспортная",
        Unit.SQ_METER,
        None,
        6000,
        shop="Местный магазин",
        form_type=MaterialForm.ROLL,
        width=120,
        roll_length=100,
    )
    # листы: пачка 10 листов 58×58 см за 600 ₸ → 60 ₸/лист, 178,36 ₸/м²
    _material(
        "Плёнка упаковочная 58×58 см",
        "Фирменная",
        Unit.SHEET,
        10,
        600,
        form_type=MaterialForm.SHEET,
        width=58,
        length=58,
    )

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
        equipment=printer,
        material=pla_white,
        batch_material_qty=D(250),
        batch_minutes=D(480),
        batch_size=1,
    )
    PartOperation.objects.create(part=shade, operation_type=_op("Снятие поддержек"), minutes=D(5))
    base = Part.objects.create(
        name="Подставка Луна",
        equipment=laser,
        material=plywood,
        batch_material_qty=D(1),
        batch_minutes=D(12),
        batch_size=4,
    )
    PartOperation.objects.create(part=base, operation_type=_op("Шлифовка"), minutes=D(2))
    fixator = Part.objects.create(
        name="Фиксатор плафона",
        equipment=printer,
        material=pla_white,
        batch_material_qty=D(50),
        batch_minutes=D(120),
        batch_size=10,
    )

    lamps = ProductCategory.objects.get(name="Настольные светильники")  # код LT из сида
    luna = ProductModel.objects.create(
        name="Светильник Луна",
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

    generators.create_material_variants(luna, [pla_white, pla_black, silk_gold])

    # Ёлочный шар: поштучно и наборами
    ball_part = Part.objects.create(
        name="Корпус шара 6 см",
        equipment=printer,
        material=pla_red,
        batch_material_qty=D(120),
        batch_minutes=D(240),
        batch_size=8,
    )
    newyear = ProductCategory.objects.get(name="Ёлочные игрушки")  # код NT из сида
    ball = ProductModel.objects.create(
        name="Ёлочный шар",
        category=newyear,
        default_packaging=small_box,
        default_packing_minutes=D(1),
    )
    ModelPart.objects.create(model=ball, part=ball_part, quantity=1, slot="Корпус")
    generators.create_material_variants(
        ball, [pla_red, pla_green, pla_violet, silk_gold], add_random=True
    )
    red, green, violet, gold, mix = (
        variant_of(ball, n) for n in ("Красный", "Зелёный", "Фиолетовый", "Золотой", "Ассорти")
    )
    generators.create_set(ball, [red], 100, packaging=big_box, packing_minutes=D(20))
    generators.create_set(ball, [mix], 100, packaging=big_box, packing_minutes=D(20))
    generators.create_set(
        ball, [red, green, violet, gold], 100, packaging=big_box, packing_minutes=D(20)
    )
    load_demo_stock()
    return {"luna": luna, "ball": ball}


@transaction.atomic
def load_demo_stock() -> None:
    """Склад: закупка, печать с браком, сборка и упаковка одной «Луны»."""
    from stock.models import (
        Assembly,
        Inventory,
        InventoryLine,
        Packing,
        ProductionRun,
        Receipt,
        ReceiptLine,
        Transfer,
        TransferLine,
        Warehouse,
    )

    luna = ProductModel.objects.get(name="Светильник Луна")
    workshop = Warehouse.objects.create(name="Цех", is_default=True)
    Warehouse.objects.create(name="Магазин", order=1)
    showcase = Warehouse.objects.create(name="Магазин — витрина", order=2, is_showcase=True)

    receipt = Receipt.objects.create(warehouse=workshop, notes="первая закупка")

    def buy(material, packs):
        """Упаковками по прошлому предложению поставщика: сумма и количество считаются сами."""
        offer = material.offers.get()
        ReceiptLine.objects.create(
            receipt=receipt,
            material=material,
            supplier=offer.supplier,
            manufacturer=offer.manufacturer,
            packs=packs,
            pack_size=offer.pack_size,
            pack_price=offer.pack_price,
        )

    for name, color, packs in (
        ("PLA Basic", "Белый", 3),
        ("PLA Basic", "Чёрный", 1),
        ("PLA Silk", "Золотой", 1),
    ):
        buy(Material.objects.get(name=name, color__name=color), packs)
    for name, packs in (("ВПП плёнка 120 см", 5), ("Плёнка упаковочная 58×58 см", 1)):
        buy(Material.objects.get(name=name), packs)
    receipt.post()

    inventory = Inventory.objects.create(
        warehouse=workshop, notes="начальные остатки комплектующих"
    )
    for name, qty in (
        ("Фанера 4 мм", 5),
        ("Шнур с выключателем", 10),
        ("Патрон E27", 10),
        ("Винт M3", 100),
        ("Коробка 20×20×25", 10),
        ("Коробочка 8×8×8", 50),
        ("Коробка 40×30×30", 5),
    ):
        InventoryLine.objects.create(
            inventory=inventory,
            material=Material.objects.get(name=name),
            counted_qty=D(qty),
            reason="начальный остаток",
        )
    inventory.post()

    white = Material.objects.get(name="PLA Basic", color__name="Белый")
    for part_name, good, scrap in (
        ("Плафон Луна", 2, 1),
        ("Фиксатор плафона", 10, 0),
        ("Подставка Луна", 4, 0),
    ):
        part = Part.objects.get(name=part_name)
        ProductionRun.objects.create(
            warehouse=workshop,
            part=part,
            equipment=part.equipment,
            material=white if part.material.name == "PLA Basic" else part.material,
            good_qty=good,
            scrap_qty=scrap,
        ).post()

    Assembly.objects.create(
        warehouse=workshop, variant=variant_of(luna, "Белый"), quantity=2
    ).post()
    Packing.objects.create(warehouse=workshop, product=product_of(luna, "Белый"), quantity=1).post()

    # выставочный образец: вторая «Луна» уезжает на витрину магазина
    transfer = Transfer.objects.create(
        warehouse=workshop, to_warehouse=showcase, notes="образец на витрину"
    )
    TransferLine.objects.create(transfer=transfer, variant=variant_of(luna, "Белый"), quantity=1)
    transfer.post()
