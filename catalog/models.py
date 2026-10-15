"""
Справочники производства: категории материалов, палитра цветов, материалы,
оборудование и типы операций.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from simple_history.models import HistoricalRecords

from core.models import (
    CodeField,
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
    )
    kind = models.CharField(
        "Вид материала",
        max_length=20,
        choices=MaterialKind.choices,
        help_text="У вложенной категории берётся от родителя.",
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

    code = CodeField(
        "Код", unique=True, max_length=10, help_text="Используется в артикуле: LUNA-WH."
    )
    hex = models.CharField(
        "Образец (HEX)",
        max_length=7,
        default="#FFFFFF",
        validators=[RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Формат #RRGGBB")],
    )
    order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Цвет"
        verbose_name_plural = "Палитра цветов"
        ordering = ["order", "name"]
        constraints = [models.UniqueConstraint(fields=["name_key"], name="catalog_color_name_uniq")]


class Material(UniqueNameModel):
    category = models.ForeignKey(
        MaterialCategory,
        verbose_name="Категория",
        on_delete=models.PROTECT,
        related_name="materials",
    )
    unit = models.CharField("Единица расхода", max_length=10, choices=Unit.choices)
    pack_size = positive_decimal_field(
        "Размер закупочной упаковки",
        help_text="В единицах расхода: катушка 1000 г → 1000; упаковка 10 шт → 10; один лист → 1.",
    )
    pack_price = money_field("Цена закупочной упаковки, ₸")
    supplier = models.CharField("Производитель / поставщик", max_length=150, blank=True)
    url = models.URLField("Ссылка у поставщика", blank=True)
    colors = models.ManyToManyField(
        Color, through="MaterialColor", verbose_name="Цвета", blank=True, related_name="materials"
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "Материал"
        verbose_name_plural = "Материалы и комплектующие"
        ordering = ["category__path", "name"]
        constraints = [
            models.UniqueConstraint(fields=["name_key"], name="catalog_material_name_uniq")
        ]

    @property
    def unit_price(self) -> Decimal:
        return self.pack_price / self.pack_size

    @property
    def kind(self) -> str:
        return self.category.kind

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
                material=self,
                pack_size=self.pack_size,
                pack_price=self.pack_price,
                unit_price=self.unit_price,
                changed_by=changed_by,
            )


class MaterialColor(models.Model):
    """Какие цвета палитры есть у материала."""

    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name="material_colors")
    color = models.ForeignKey(
        Color,
        verbose_name="Цвет",
        on_delete=models.PROTECT,
    )
    vendor_code = models.CharField("Код у производителя", max_length=50, blank=True)
    available = models.BooleanField("Есть в продаже", default=True)

    class Meta:
        verbose_name = "Цвет материала"
        verbose_name_plural = "Цвета материала"
        ordering = ["color__order", "color__name"]
        constraints = [
            models.UniqueConstraint(fields=["material", "color"], name="catalog_materialcolor_uniq")
        ]

    def __str__(self):
        return f"{self.material} — {self.color}"


class MaterialPriceHistory(models.Model):
    """История цен материала. Заполняется автоматически, не редактируется."""

    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="price_history")
    changed_at = models.DateTimeField("Дата", auto_now_add=True)
    pack_size = models.DecimalField("Размер упаковки", max_digits=14, decimal_places=4)
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

    technology = models.CharField("Технология", max_length=20, choices=Technology.choices)
    quantity = models.PositiveIntegerField(
        "Количество, шт", default=1, validators=[MinValueValidator(1)]
    )
    purchase_price = money_field("Цена покупки одного, ₸")
    maintenance_cost = money_field(
        "Обслуживание за весь срок, ₸",
        default=Decimal(0),
        help_text="Сопла, ремни, хотэнды, пластины; для лазера — трубки, линзы, зеркала.",
    )
    residual_value = money_field("Остаточная стоимость, ₸", default=Decimal(0))
    resource_hours = models.DecimalField(
        "Ресурс, ч", max_digits=10, decimal_places=1, validators=[MinValueValidator(Decimal(1))]
    )
    power_kw = models.DecimalField(
        "Средняя мощность, кВт",
        max_digits=6,
        decimal_places=3,
        validators=[MinValueValidator(Decimal(0))],
        help_text="Средняя, а не с шильдика. Точнее всего — замер ваттметром из розетки.",
    )
    scrap_percent = percent_field(
        "% брака", null=True, blank=True, help_text="Пусто — из общих настроек."
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
    level = models.CharField("Уровень", max_length=10, choices=OperationLevel.choices)
    rate = money_field(
        "Ставка, ₸/ч", null=True, blank=True, help_text="Пусто — ставка из общих настроек."
    )
    default_minutes = models.DecimalField(
        "Время по умолчанию, мин",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
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
