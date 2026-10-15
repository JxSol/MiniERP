"""
Справочники производства: категории материалов, палитра цветов, производители
и поставщики, материалы с предложениями поставщиков, оборудование, типы операций.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from simple_history.models import HistoricalRecords

from core.models import (
    SiteSettings,
    UniqueNameModel,
    money_field,
    percent_field,
    positive_decimal_field,
)


class MaterialKind(models.TextChoices):
    FILAMENT = "filament", "Пластик для FDM-печати"
    RESIN = "resin", "Смола"
    SHEET = "sheet", "Листовой материал"
    COMPONENT = "component", "Комплектующие"
    FINISH = "finish", "Отделка"
    PACKAGING = "packaging", "Упаковка"
    CONSUMABLE = "consumable", "Расходники"


# Из чего можно изготавливать детали и что можно покупать как комплектующие
PRODUCTION_KINDS = (MaterialKind.FILAMENT, MaterialKind.RESIN, MaterialKind.SHEET)
COMPONENT_KINDS = (MaterialKind.COMPONENT, MaterialKind.FINISH, MaterialKind.CONSUMABLE)


class Unit(models.TextChoices):
    GRAM = "g", "г"
    ML = "ml", "мл"
    PIECE = "pcs", "шт"
    METER = "m", "м"
    SQ_METER = "m2", "м²"
    SHEET = "sheet", "лист"


class Technology(models.TextChoices):
    FDM = "fdm", "FDM-печать"
    RESIN = "resin", "Смоляная печать"
    LASER_CO2 = "laser_co2", "Лазер CO₂"
    LASER_FIBER = "laser_fiber", "Лазер волоконный"
    OTHER = "other", "Другое"


# Обозначение технологии в коде детали: C-FDM-0000001
TECHNOLOGY_CODES = {
    Technology.FDM: "FDM",
    Technology.RESIN: "SLA",
    Technology.LASER_CO2: "CO2",
    Technology.LASER_FIBER: "FIBER",
    Technology.OTHER: "OTHER",
}

# Какие материалы подходят к какой технологии (ТЗ п. 5.6, правило 7)
TECHNOLOGY_MATERIALS = {
    Technology.FDM: {MaterialKind.FILAMENT},
    Technology.RESIN: {MaterialKind.RESIN},
    Technology.LASER_CO2: {MaterialKind.SHEET},
    Technology.LASER_FIBER: {MaterialKind.SHEET},
    Technology.OTHER: set(PRODUCTION_KINDS),
}


class MaterialCategory(UniqueNameModel):
    """Дерево категорий. Вид материала задаётся у корня и наследуется детьми."""

    parent = models.ForeignKey(
        "self",
        verbose_name="Родительская категория",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
        help_text="Пусто — категория верхнего уровня.",
    )
    kind = models.CharField(
        "Вид материала",
        max_length=20,
        choices=MaterialKind.choices,
        help_text="Определяет, на каких станках материал можно использовать. "
        "У вложенной категории берётся от родителя.",
    )
    path = models.CharField("Полное название", max_length=500, editable=False, db_index=True)

    name_scope = ("parent",)

    class Meta:
        verbose_name = "Категория материалов"
        verbose_name_plural = "Категории материалов"
        ordering = ["path"]
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "name_key"],
                name="catalog_materialcategory_name_uniq",
                nulls_distinct=False,
            )
        ]

    def __str__(self):
        return self.path or self.name

    def clean(self):
        super().clean()
        node = self.parent
        while node is not None:
            if node.pk == self.pk:
                raise ValidationError({"parent": "Категория не может быть вложена сама в себя."})
            node = node.parent

    def save(self, *args, **kwargs):
        if self.parent_id:
            self.kind = self.parent.kind
        self.path = f"{self.parent.path} → {self.name}" if self.parent_id else self.name
        super().save(*args, **kwargs)
        for child in self.children.all():
            child.save()


class Color(UniqueNameModel):
    """Единая палитра цветов. Ведёт только администратор (ТЗ п. 5.6)."""

    hex = models.CharField(
        "Образец (HEX)",
        max_length=7,
        default="#FFFFFF",
        validators=[RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Формат #RRGGBB")],
        help_text="Цвет кружка-образца в списках, например #388E3C.",
    )
    order = models.PositiveIntegerField("Порядок", default=0, help_text="Порядок в списках выбора.")

    class Meta:
        verbose_name = "Цвет"
        verbose_name_plural = "Палитра цветов"
        ordering = ["order", "name"]
        constraints = [models.UniqueConstraint(fields=["name_key"], name="catalog_color_name_uniq")]


class Manufacturer(UniqueNameModel):
    """Производитель материала: Bambu Lab, eSun, Polymaker…"""

    url = models.URLField("Сайт", blank=True)

    class Meta:
        verbose_name = "Производитель"
        verbose_name_plural = "Производители"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name_key"], name="catalog_manufacturer_name_uniq")
        ]


class Supplier(UniqueNameModel):
    """Где покупаем: Kaspi, Ozon, AliExpress, местный магазин…"""

    url = models.URLField("Сайт", blank=True)

    class Meta:
        verbose_name = "Поставщик"
        verbose_name_plural = "Поставщики"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name_key"], name="catalog_supplier_name_uniq")
        ]


class Material(UniqueNameModel):
    """
    Конкретный материал, которым печатаем или который кладём в изделие:
    «PLA Basic, зелёный», «Фанера 4 мм», «Шнур с выключателем».
    Цена — среднее по актуальным предложениям поставщиков (п. 4.2 ТЗ).
    """

    category = models.ForeignKey(
        MaterialCategory,
        verbose_name="Категория",
        on_delete=models.PROTECT,
        related_name="materials",
        help_text="От категории зависит, на каких станках материал можно использовать.",
    )
    color = models.ForeignKey(
        Color,
        verbose_name="Цвет",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="materials",
        help_text="Из палитры. Для фанеры, шнуров и упаковки можно не указывать.",
    )
    unit = models.CharField(
        "Единица расхода",
        max_length=10,
        choices=Unit.choices,
        help_text="В чём считается расход в деталях: граммы для пластика, листы для фанеры, штуки.",
    )

    history = HistoricalRecords()

    # Название уникально вместе с цветом: «PLA Basic» может быть зелёным и красным
    name_scope = ("color",)

    class Meta:
        verbose_name = "Материал"
        verbose_name_plural = "Материалы и комплектующие"
        ordering = ["category__path", "name", "color__order"]
        constraints = [
            models.UniqueConstraint(
                fields=["name_key", "color"],
                name="catalog_material_name_uniq",
                nulls_distinct=False,
            )
        ]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self) -> str:
        if self.color_id:
            return f"{self.name}, {self.color.name.lower()}"
        return self.name

    @property
    def kind(self) -> str:
        return self.category.kind

    @property
    def active_offers(self):
        """Актуальные предложения; использует prefetch, если он был."""
        if "offers" in getattr(self, "_prefetched_objects_cache", {}):
            return [o for o in self.offers.all() if o.is_active]
        return list(self.offers.filter(is_active=True))

    @property
    def unit_price(self) -> Decimal | None:
        """Среднее по актуальным предложениям. None — цены ещё нет."""
        prices = [o.unit_price for o in self.active_offers]
        if not prices:
            return None
        return sum(prices, Decimal(0)) / len(prices)

    @property
    def line_key(self) -> str:
        """Ключ линейки без цвета: «PLA Basic» объединяет все цвета (для «Ассорти»)."""
        return self.name_key

    def clean(self):
        super().clean()
        if not self.name:
            return
        # Название линейки одно для всех цветов, поэтому и категория с единицей должны совпадать
        sibling = (
            Material.objects.filter(name_key=self.name_key_value())
            .exclude(pk=self.pk)
            .select_related("category")
            .first()
        )
        if sibling and self.category_id and sibling.category_id != self.category_id:
            raise ValidationError(
                {"category": f"«{sibling.name}» уже заведён в категории «{sibling.category}»."}
            )
        if sibling and self.unit and sibling.unit != self.unit:
            raise ValidationError(
                {"unit": f"У «{sibling.name}» единица расхода — {sibling.get_unit_display()}."}
            )

    def name_key_value(self) -> str:
        from core.models import name_key

        return name_key(self.name)


class MaterialOffer(models.Model):
    """
    Предложение поставщика: у кого, по какой цене и в какой упаковке покупаем материал.
    Галочка «Актуально» включает предложение в расчёт средней цены.
    """

    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name="offers")
    manufacturer = models.ForeignKey(
        Manufacturer,
        verbose_name="Производитель",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="offers",
        help_text="Чей это материал. Можно не указывать для крепежа и упаковки.",
    )
    supplier = models.ForeignKey(
        Supplier,
        verbose_name="Поставщик",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="offers",
        help_text="Где покупаем.",
    )
    pack_size = positive_decimal_field(
        "Упаковка",
        help_text="Сколько единиц расхода в одной упаковке: катушка 1000 г → 1000; 10 шт → 10; лист → 1.",
    )
    pack_price = money_field("Цена упаковки, ₸", help_text="Сколько стоит одна упаковка.")
    url = models.URLField("Ссылка", blank=True, help_text="Страница товара у поставщика.")
    is_active = models.BooleanField(
        "Актуально",
        default=True,
        help_text="Только актуальные предложения участвуют в расчёте средней цены. "
        "Перешли на другой пластик — снимите галочку со старого.",
    )
    notes = models.CharField("Примечание", max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Предложение поставщика"
        verbose_name_plural = "Предложения поставщиков"
        ordering = ["-is_active", "manufacturer__name", "supplier__name"]

    def __str__(self):
        who = " · ".join(str(x) for x in (self.manufacturer, self.supplier) if x)
        return f"{who or 'без поставщика'}: {self.pack_size:f} за {self.pack_price} ₸"

    @property
    def unit_price(self) -> Decimal:
        return self.pack_price / self.pack_size

    def save(self, *args, **kwargs):
        changed_by = getattr(self, "_changed_by", None)
        price_changed = True
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).values("pack_size", "pack_price").first()
            price_changed = old is None or (
                old["pack_size"] != self.pack_size or old["pack_price"] != self.pack_price
            )
        super().save(*args, **kwargs)
        if price_changed:
            MaterialPriceHistory.objects.create(
                offer=self,
                material=self.material,
                pack_size=self.pack_size,
                pack_price=self.pack_price,
                unit_price=self.unit_price,
                changed_by=changed_by,
            )


class MaterialPriceHistory(models.Model):
    """История цен по предложениям. Заполняется автоматически, не редактируется."""

    offer = models.ForeignKey(MaterialOffer, on_delete=models.PROTECT, related_name="price_history")
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="price_history")
    changed_at = models.DateTimeField("Дата", auto_now_add=True)
    pack_size = models.DecimalField("Упаковка", max_digits=14, decimal_places=4)
    pack_price = models.DecimalField("Цена упаковки, ₸", max_digits=14, decimal_places=2)
    unit_price = models.DecimalField("Цена за единицу, ₸", max_digits=18, decimal_places=6)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Кто изменил",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        verbose_name = "Цена материала"
        verbose_name_plural = "История цен материалов"
        ordering = ["-changed_at"]

    def __str__(self):
        return f"{self.material}: {self.pack_price} ₸ от {self.changed_at:%d.%m.%Y}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Историю цен изменить нельзя.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Историю цен удалить нельзя.")


class Equipment(UniqueNameModel):
    """Тип станка (не конкретный экземпляр): деталь можно сделать на любом из них."""

    technology = models.CharField(
        "Технология",
        max_length=20,
        choices=Technology.choices,
        help_text="Определяет подходящие материалы и обозначение в коде детали (C-FDM-…).",
    )
    quantity = models.PositiveIntegerField(
        "Количество, шт",
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Сколько таких станков. На расчёт не влияет, нужно для планирования.",
    )
    purchase_price = money_field("Цена покупки одного, ₸", help_text="Сколько стоил станок.")
    maintenance_cost = money_field(
        "Обслуживание за весь срок, ₸",
        default=Decimal(0),
        help_text="Сопла, ремни, хотэнды, пластины; для лазера — трубки, линзы, зеркала.",
    )
    residual_value = money_field(
        "Остаточная стоимость, ₸",
        default=Decimal(0),
        help_text="За сколько можно продать станок в конце срока. Обычно 0.",
    )
    resource_hours = models.DecimalField(
        "Ресурс, ч",
        max_digits=10,
        decimal_places=1,
        validators=[MinValueValidator(Decimal(1))],
        help_text="Сколько часов станок отработает до замены. Для принтера ~5 000.",
    )
    power_kw = models.DecimalField(
        "Средняя мощность, кВт",
        max_digits=6,
        decimal_places=3,
        validators=[MinValueValidator(Decimal(0))],
        help_text="Средняя, а не с шильдика. Точнее всего — замер ваттметром из розетки.",
    )
    scrap_percent = percent_field(
        "% брака",
        null=True,
        blank=True,
        help_text="Доля неудачных запусков на этом станке. Пусто — из общих настроек.",
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "Оборудование"
        verbose_name_plural = "Оборудование"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name_key"], name="catalog_equipment_name_uniq")
        ]

    def clean(self):
        super().clean()
        if (
            self.residual_value is not None
            and self.purchase_price is not None
            and self.residual_value > self.purchase_price + (self.maintenance_cost or 0)
        ):
            raise ValidationError(
                {"residual_value": "Остаточная стоимость больше затрат на станок."}
            )

    @property
    def technology_code(self) -> str:
        return TECHNOLOGY_CODES[self.technology]

    @property
    def amortization_per_hour(self) -> Decimal:
        return (
            self.purchase_price + self.maintenance_cost - self.residual_value
        ) / self.resource_hours

    @property
    def electricity_per_hour(self) -> Decimal:
        return self.power_kw * SiteSettings.load().electricity_tariff

    @property
    def effective_scrap_percent(self) -> Decimal:
        if self.scrap_percent is not None:
            return self.scrap_percent
        return SiteSettings.load().default_scrap_percent

    @property
    def allowed_material_kinds(self) -> set[str]:
        return TECHNOLOGY_MATERIALS[self.technology]


class OperationLevel(models.TextChoices):
    PART = "part", "Деталь — обработка"
    MODEL = "model", "Модель — сборка"
    PRODUCT = "product", "Товар — комплектация"
    PACKING = "packing", "Доупаковка"


class OperationType(UniqueNameModel):
    level = models.CharField(
        "Уровень",
        max_length=10,
        choices=OperationLevel.choices,
        help_text="Где операция применяется: обработка детали, сборка модели или комплектация товара.",
    )
    rate = money_field(
        "Ставка, ₸/ч",
        null=True,
        blank=True,
        help_text="Стоимость часа этой работы. Пусто — ставка из общих настроек.",
    )
    default_minutes = models.DecimalField(
        "Время по умолчанию, мин",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Подставляется, когда операцию добавляет система (упаковка, комплектация).",
    )
    key = models.SlugField(
        "Системный ключ",
        max_length=30,
        unique=True,
        null=True,
        blank=True,
        editable=False,
        help_text="Для операций, которые система подставляет сама (упаковка, комплектация).",
    )
    order = models.PositiveIntegerField("Порядок", default=0)

    history = HistoricalRecords()

    class Meta:
        verbose_name = "Тип операции"
        verbose_name_plural = "Типы операций"
        ordering = ["level", "order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["name_key"], name="catalog_operationtype_name_uniq")
        ]

    def effective_rate(self) -> Decimal:
        return self.rate if self.rate is not None else SiteSettings.load().labor_rate
