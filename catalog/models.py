"""
Справочники производства: категории материалов, палитра цветов, производители
и поставщики, материалы с предложениями поставщиков, оборудование, типы операций.
"""

import re
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
    LINEAR_METER = "lm", "пог. м"


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


class MaterialForm(models.TextChoices):
    """Форма учёта: определяет единицу расхода, нужные размеры и единицы ввода количества."""

    PLAIN = "plain", "Обычный (по единице расхода)"
    PIECE = "piece", "Штучный с габаритами"
    SHEET = "sheet", "Листовой"
    ROLL = "roll", "Рулонный"
    LENGTH = "length", "Погонный (кабель, верёвка)"


# Единица расхода, которую форма учёта задаёт сама (обычная форма выбирает единицу вручную)
FORM_UNITS = {
    MaterialForm.PIECE: Unit.PIECE,
    MaterialForm.SHEET: Unit.SHEET,
    MaterialForm.ROLL: Unit.LINEAR_METER,
    MaterialForm.LENGTH: Unit.METER,
}


class QtyUnit(models.TextChoices):
    """Альтернативные единицы ввода количества; в базе всегда единица расхода материала."""

    SQ_METER = "m2", "м²"
    LINEAR_METER = "lm", "пог. м"


class EnteredUnitMixin(models.Model):
    """Строка с количеством материала: хранится в единице расхода, но помним, в чём ввели."""

    entered_unit = models.CharField(
        "Единица ввода",
        max_length=4,
        choices=QtyUnit.choices,
        blank=True,
        default="",
        help_text="Пусто — единица расхода материала. Листовой можно вводить в м², "
        "рулонный — в пог. м.",
    )

    class Meta:
        abstract = True

    def quantity_display(self, quantity=None) -> str:
        from core.admin_utils import fmt_quantity

        qty = self.quantity if quantity is None else quantity
        material = getattr(self, "material", None)
        if material is not None:
            return material.format_entered(qty, self.entered_unit)
        return fmt_quantity(qty, "шт")


CIPHER_RE = re.compile(r"[0-9A-F]{3}")
# Похожие кириллические буквы и «О» вместо нуля: шифр набирают на любой раскладке
CIPHER_LOOKALIKES = str.maketrans("АВСЕОавсео", "ABCE0ABCE0")


def normalize_cipher(value: str | None) -> str:
    """«01a» → «01A», кириллические А/В/С/Е и О → латиница и ноль."""
    return (value or "").strip().upper().translate(CIPHER_LOOKALIKES)


def next_free_cipher() -> str:
    """Наименьший свободный шифр от 001 до FFF."""
    used = set(Material.objects.exclude(cipher="").values_list("cipher", flat=True))
    return next((f"{n:03X}" for n in range(1, 0x1000) if f"{n:03X}" not in used), "")


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
    )
    color = models.ForeignKey(
        Color,
        verbose_name="Цвет",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="materials",
        help_text="Для фанеры, шнуров и упаковки можно не указывать.",
    )
    cipher = models.CharField(
        "Шифр",
        max_length=3,
        blank=True,
        help_text="Пишется на образцах вместо названия, чтобы посторонние не узнали материал.",
    )
    form_type = models.CharField(
        "Форма учёта",
        max_length=10,
        choices=MaterialForm.choices,
        default=MaterialForm.PLAIN,
    )
    unit = models.CharField(
        "Единица расхода",
        max_length=10,
        choices=Unit.choices,
    )
    width_cm = models.DecimalField(
        "Ширина, см",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    length_cm = models.DecimalField(
        "Длина, см",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    height_cm = models.DecimalField(
        "Высота, см",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    roll_length_m = models.DecimalField(
        "Длина рулона, м",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    min_stock = models.DecimalField(
        "Минимальный остаток",
        max_digits=14,
        decimal_places=4,
        default=Decimal(0),
        validators=[MinValueValidator(Decimal(0))],
        help_text="0 — не следить.",
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
            ),
            models.UniqueConstraint(
                fields=["cipher"],
                condition=~models.Q(cipher=""),
                name="catalog_material_cipher_uniq",
            ),
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
        prices = [o.unit_price for o in self.active_offers if o.unit_price > 0]
        if not prices:
            return None
        return sum(prices, Decimal(0)) / len(prices)

    @property
    def line_key(self) -> str:
        """Ключ линейки без цвета: «PLA Basic» объединяет все цвета (для «Ассорти»)."""
        return self.name_key

    def clean(self):
        super().clean()
        self.cipher = normalize_cipher(self.cipher)
        if self.cipher and not CIPHER_RE.fullmatch(self.cipher):
            raise ValidationError(
                {"cipher": "Шифр — ровно три знака из 0–9 и A–F, например 01A или F3C."}
            )
        if self.form_type in FORM_UNITS:
            self.unit = FORM_UNITS[self.form_type]
        if self.form_type == MaterialForm.SHEET and not (self.width_cm and self.length_cm):
            raise ValidationError(
                {"width_cm": "Для листового материала укажите ширину и длину листа."}
            )
        if self.form_type == MaterialForm.ROLL and not self.width_cm:
            raise ValidationError({"width_cm": "Для рулонного материала укажите ширину рулона."})
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
        if sibling and sibling.form_type != self.form_type:
            raise ValidationError(
                {
                    "form_type": f"У «{sibling.name}» форма учёта — {sibling.get_form_type_display()}."
                }
            )

    def name_key_value(self) -> str:
        from core.models import name_key

        return name_key(self.name)

    # --- форма учёта: размеры, производные единицы и цены ---

    @property
    def width_m(self) -> Decimal | None:
        return self.width_cm / 100 if self.width_cm else None

    @property
    def sheet_area_m2(self) -> Decimal | None:
        """Площадь листа, м² (листовой материал)."""
        if self.form_type != MaterialForm.SHEET or not (self.width_cm and self.length_cm):
            return None
        return self.width_cm * self.length_cm / 10000  # точно: размеры с 2 знаками

    @property
    def piece_volume_m3(self) -> Decimal | None:
        """Объём одной штуки, м³ (штучный материал с тремя размерами)."""
        if self.form_type != MaterialForm.PIECE:
            return None
        if not (self.width_cm and self.length_cm and self.height_cm):
            return None
        return (self.width_cm * self.length_cm * self.height_cm / 1000000).quantize(
            Decimal("0.000001")
        )

    @property
    def roll_area_m2(self) -> Decimal | None:
        """Площадь одного рулона, м² = ширина × длина рулона."""
        if self.form_type != MaterialForm.ROLL or not (self.width_cm and self.roll_length_m):
            return None
        return (self.width_m * self.roll_length_m).quantize(Decimal("0.0001"))

    def alt_units(self) -> list[str]:
        """В каких единицах, кроме единицы расхода, можно вводить количество."""
        if self.form_type in (MaterialForm.SHEET, MaterialForm.ROLL):
            return [QtyUnit.SQ_METER]
        return []

    def _per_m2(self) -> Decimal | None:
        """Сколько единиц расхода в одном м²: лист → 1 / площадь листа, рулон → 1 / ширина."""
        if self.form_type == MaterialForm.SHEET and self.sheet_area_m2:
            return Decimal(1) / self.sheet_area_m2
        if self.form_type == MaterialForm.ROLL and self.width_m:
            return Decimal(1) / self.width_m
        return None

    def to_base(self, quantity: Decimal, unit: str = "") -> Decimal:
        """Введённое количество → единица расхода. unit пустой или равный единице — как есть."""
        if not unit or unit == self.unit:
            return quantity
        if unit == QtyUnit.SQ_METER and self.form_type in (MaterialForm.SHEET, MaterialForm.ROLL):
            per_m2 = self._per_m2()
            if per_m2 is None:
                raise ValidationError(
                    "Укажите размеры листа в карточке материала."
                    if self.form_type == MaterialForm.SHEET
                    else "Укажите ширину рулона в карточке материала."
                )
            result = (Decimal(quantity) * per_m2).quantize(Decimal("0.0001"))
            if result <= 0:
                raise ValidationError(
                    f"После перевода в {self.get_unit_display()} получается 0 — укажите "
                    f"количество крупнее или сразу в {self.get_unit_display()}."
                )
            return result
        allowed = " или ".join(
            [self.get_unit_display(), *(QtyUnit(u).label for u in self.alt_units())]
        )
        raise ValidationError(f"Для «{self}» количество вводится в {allowed}.")

    def from_base(self, quantity: Decimal, unit: str = "") -> Decimal:
        """Единица расхода → единица ввода (обратно к to_base)."""
        if not unit or unit == self.unit:
            return quantity
        per_m2 = self._per_m2()
        if unit == QtyUnit.SQ_METER and per_m2:
            return (Decimal(quantity) / per_m2).quantize(Decimal("0.0001"))
        return quantity

    def alt_factor(self) -> tuple[Decimal, str] | None:
        """Множитель «единица расхода → вторая единица»: лист → м², пог. м → м², шт → м³."""
        per_m2 = self._per_m2()
        if per_m2:
            return Decimal(1) / per_m2, "м²"
        if self.piece_volume_m3:
            return self.piece_volume_m3, "м³"
        return None

    def alt_quantity(self, quantity: Decimal) -> tuple[Decimal, str] | None:
        """Эквивалент количества во второй единице: лист → м², пог. м → м², шт → м³."""
        if quantity is None:
            return None
        alt = self.alt_factor()
        if alt is None:
            return None
        factor, label = alt
        return (Decimal(quantity) * factor).quantize(Decimal("0.0001")), label

    @property
    def direct_unit(self) -> tuple[str, Decimal]:
        """Единица ввода «напрямую» в закупке и сколько в ней единиц расхода: кг → 1000 г."""
        if self.unit == Unit.GRAM:
            return "кг", Decimal(1000)
        if self.unit == Unit.ML:
            return "л", Decimal(1000)
        return self.get_unit_display(), Decimal(1)

    def format_qty(self, quantity: Decimal | None) -> str:
        """«10 лист (3,364 м²)», «500 пог. м (600 м²)», «10 шт (0,9 м³)»."""
        from core.admin_utils import fmt_quantity

        base = fmt_quantity(quantity, self.get_unit_display())
        alt = self.alt_quantity(quantity)
        return f"{base} ({fmt_quantity(*alt)})" if alt else base

    def format_entered(self, quantity: Decimal, unit: str = "") -> str:
        """Как ввели + в единице расхода: «60 м² (50 пог. м)»."""
        from core.admin_utils import fmt_quantity

        if unit and unit != self.unit:
            entered = fmt_quantity(self.from_base(quantity, unit), QtyUnit(unit).label)
            return f"{entered} ({fmt_quantity(quantity, self.get_unit_display())})"
        return self.format_qty(quantity)

    def price_breakdown(self, unit_price: Decimal | None) -> list[tuple[Decimal, str]]:
        """Цена за единицу расхода и производная: ₸/лист + ₸/м², ₸/пог. м + ₸/м², ₸/шт + ₸/м³."""
        if unit_price is None:
            return []
        result = [(unit_price, self.get_unit_display())]
        per_m2 = self._per_m2()
        if per_m2:
            result.append((unit_price * per_m2, "м²"))
        elif self.piece_volume_m3:
            result.append((unit_price / self.piece_volume_m3, "м³"))
        return result

    def unit_prices(self) -> list[tuple[Decimal, str]]:
        return self.price_breakdown(self.unit_price)


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
        help_text="Сколько единиц расхода в одной упаковке: катушка 1000 г → 1000; 10 шт → 10; "
        "пачка 10 листов → 10; бухта кабеля 100 м → 100. Для рулона подставляется длина "
        "рулона в пог. м.",
    )
    pack_price = money_field(
        "Цена упаковки, ₸",
        validators=[MinValueValidator(Decimal("0.01"))],  # бесплатного материала не бывает
        help_text="Сколько стоит одна упаковка.",
    )
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
        from core.admin_utils import fmt_price

        pack = self.material.format_qty(self.pack_size)
        return f"{who or 'без поставщика'} — {pack} за {fmt_price(self.pack_price)}"

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
        from core.admin_utils import fmt_price

        return (
            f"{self.material}: {self.material.format_qty(self.pack_size)} за "
            f"{fmt_price(self.pack_price)} от {self.changed_at:%d.%m.%Y}"
        )

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
    )
    quantity = models.PositiveIntegerField(
        "Количество, шт",
        default=1,
        validators=[MinValueValidator(1)],
    )
    purchase_price = money_field("Цена покупки одного, ₸")
    maintenance_cost = money_field(
        "Обслуживание за весь срок, ₸",
        default=Decimal(0),
    )
    residual_value = money_field(
        "Остаточная стоимость, ₸",
        default=Decimal(0),
    )
    resource_hours = models.DecimalField(
        "Ресурс, ч",
        max_digits=10,
        decimal_places=1,
        validators=[MinValueValidator(Decimal(1))],
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
        help_text="Пусто — из общих настроек.",
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
    )
    rate = money_field(
        "Ставка, ₸/ч",
        null=True,
        blank=True,
        help_text="Пусто — ставка из общих настроек.",
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
