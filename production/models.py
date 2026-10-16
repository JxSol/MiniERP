"""
Производство: детали, модели и их варианты (ТЗ, разделы 3 и 6.5–6.7).
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from simple_history.models import HistoricalRecords

from catalog.models import (
    COMPONENT_KINDS,
    PRODUCTION_KINDS,
    Equipment,
    Material,
    MaterialKind,
    OperationLevel,
    OperationType,
    Unit,
)
from core.models import (
    ArchivableModel,
    CodeField,
    Size,
    Tag,
    UniqueNameModel,
    clean_name,
    money_field,
    name_key,
    next_code,
    percent_field,
    positive_decimal_field,
)


def clean_slot(value: str) -> str:
    """Название слота: без лишних пробелов, с заглавной буквы."""
    value = clean_name(value)
    return value[:1].upper() + value[1:] if value else ""


def _minutes_field(verbose_name="Время на 1 шт, мин"):
    return models.DecimalField(
        verbose_name,
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )


def _rate_field():
    return money_field(
        "Ставка, ₸/ч", null=True, blank=True, help_text="Пусто — из типа операции или настроек."
    )


# --- Категории изделий -----------------------------------------------------------


class ProductCategory(UniqueNameModel):
    parent = models.ForeignKey(
        "self",
        verbose_name="Родительская категория",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    path = models.CharField("Полное название", max_length=500, editable=False, db_index=True)

    name_scope = ("parent",)

    class Meta:
        verbose_name = "Категория изделий"
        verbose_name_plural = "Категории изделий"
        ordering = ["path"]
        constraints = [
            models.UniqueConstraint(
                fields=["parent", "name_key"],
                name="production_productcategory_name_uniq",
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
        self.path = f"{self.parent.path} → {self.name}" if self.parent_id else self.name
        super().save(*args, **kwargs)
        for child in self.children.all():
            child.save()


# --- Деталь ----------------------------------------------------------------------


class Part(UniqueNameModel):
    """То, что изготавливается на одном станке за один проход (ТЗ п. 6.5)."""

    code = CodeField("Код", unique=True)
    equipment = models.ForeignKey(
        Equipment,
        verbose_name="Станок",
        on_delete=models.PROTECT,
        related_name="parts",
        help_text="На чём изготавливается. Технология станка попадает в код детали (C-FDM-…); "
        "поменять технологию у готовой детали нельзя — это уже другая деталь.",
    )
    material = models.ForeignKey(
        Material,
        verbose_name="Материал по умолчанию",
        on_delete=models.PROTECT,
        related_name="parts",
        limit_choices_to={"category__kind__in": PRODUCTION_KINDS},
        help_text="В вариантах модели материал детали со слотом можно заменить на совместимый.",
    )
    batch_material_qty = positive_decimal_field(
        "Расход материала на партию",
        help_text="Как показывает слайсер или программа лазера — на весь стол или лист.",
    )
    batch_minutes = models.DecimalField(
        "Время на партию",
        max_digits=10,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Хранится в минутах; вводится как дни, часы, минуты, секунды.",
    )
    batch_size = models.PositiveIntegerField(
        "Штук в партии",
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Сколько деталей выходит со стола или с листа.",
    )
    scrap_percent = percent_field("% брака", null=True, blank=True, help_text="Пусто — из станка.")
    model_file = models.FileField("Файл модели", upload_to="part_files/", blank=True)
    model_url = models.URLField("Ссылка на файл модели", blank=True)
    print_params = models.TextField("Параметры печати / резки", blank=True)

    cost_cached = models.DecimalField(
        "Себестоимость 1 шт, ₸", max_digits=14, decimal_places=4, null=True, editable=False
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "Деталь"
        verbose_name_plural = "Детали"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name_key"], name="production_part_name_uniq")
        ]

    def clean(self):
        super().clean()
        if self.pk and self.code and self.equipment_id:
            old_tech = self.code.split("-")[1]
            if old_tech != self.equipment.technology_code:
                raise ValidationError(
                    {
                        "equipment": "Смена технологии — это новая деталь. "
                        "Нажмите «Копировать деталь» и выберите другой станок."
                    }
                )
        if self.equipment_id and self.material_id:
            if self.material.kind not in self.equipment.allowed_material_kinds:
                raise ValidationError(
                    {
                        "material": f"Материал «{self.material}» не подходит для станка "
                        f"«{self.equipment}» ({self.equipment.get_technology_display()})."
                    }
                )

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = next_code(f"C-{self.equipment.technology_code}")
        super().save(*args, **kwargs)

    @property
    def material_per_piece(self) -> Decimal:
        return self.batch_material_qty / self.batch_size

    @property
    def hours_per_piece(self) -> Decimal:
        return self.batch_minutes / 60 / self.batch_size


class PartOperation(models.Model):
    part = models.ForeignKey(Part, on_delete=models.CASCADE, related_name="operations")
    operation_type = models.ForeignKey(
        OperationType,
        verbose_name="Операция",
        on_delete=models.PROTECT,
        limit_choices_to={"level": OperationLevel.PART},
    )
    minutes = _minutes_field()
    rate = _rate_field()

    class Meta:
        verbose_name = "Обработка детали"
        verbose_name_plural = "Обработка детали"

    def __str__(self):
        return str(self.operation_type)


# --- Модель ----------------------------------------------------------------------


class ModelStatus(models.TextChoices):
    DRAFT = "draft", "В разработке"
    ACTIVE = "active", "Активна"
    RETIRED = "retired", "Снята с производства"


class ProductModel(UniqueNameModel):
    """Изделие целиком: детали, покупные комплектующие, сборка (ТЗ п. 6.6)."""

    code = CodeField("Код", unique=True)
    category = models.ForeignKey(
        ProductCategory,
        verbose_name="Категория",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="models",
    )
    tags = models.ManyToManyField(Tag, verbose_name="Метки", blank=True)
    status = models.CharField(
        "Статус", max_length=10, choices=ModelStatus.choices, default=ModelStatus.DRAFT
    )
    description = models.TextField(
        "Описание", blank=True, help_text="База для описаний на площадках."
    )
    default_packaging = models.ForeignKey(
        Material,
        verbose_name="Упаковка по умолчанию",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        limit_choices_to={"category__kind": MaterialKind.PACKAGING},
        help_text="Подставляется в поштучные товары этой модели.",
    )
    default_packing_minutes = models.DecimalField(
        "Время упаковки по умолчанию, мин",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    weight_g = models.PositiveIntegerField(
        "Масса изделия, г",
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text="Готовое изделие без упаковки, по весам. Рядом — расчётная масса пластика "
        "по нормам деталей, для сверки.",
    )
    length_cm = models.DecimalField(
        "Длина, см",
        max_digits=7,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.1"))],
        help_text="Габариты готового изделия без упаковки. Габариты в упаковке — у товара.",
    )
    width_cm = models.DecimalField(
        "Ширина, см",
        max_digits=7,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.1"))],
    )
    height_cm = models.DecimalField(
        "Высота, см",
        max_digits=7,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.1"))],
    )
    base_model = models.ForeignKey(
        "self",
        verbose_name="Базовая модель",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="sizes",
        help_text="Для размерных моделей: «Конструктор XL» → базовая «Конструктор».",
    )
    size = models.ForeignKey(
        Size,
        verbose_name="Размер",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    marketplace_group = models.CharField(
        "Группа на площадках",
        max_length=100,
        blank=True,
        help_text="Код объединения карточек вариантов и размеров на площадках.",
    )

    cost_cached = models.DecimalField(
        "Себестоимость (материалы по умолчанию), ₸",
        max_digits=14,
        decimal_places=4,
        null=True,
        editable=False,
    )

    history = HistoricalRecords(m2m_fields=[tags])

    class Meta:
        verbose_name = "Модель"
        verbose_name_plural = "Модели"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["name_key"], name="production_productmodel_name_uniq"),
            models.UniqueConstraint(
                fields=["base_model", "size"], name="production_productmodel_size_uniq"
            ),
        ]

    def clean(self):
        super().clean()
        if self.base_model_id and not self.size_id:
            raise ValidationError({"size": "У размерной модели должен быть указан размер."})
        if self.size_id and not self.base_model_id:
            raise ValidationError(
                {"base_model": "Размер указывается только вместе с базовой моделью."}
            )
        if self.base_model_id:
            if self.base_model_id == self.pk:
                raise ValidationError({"base_model": "Модель не может быть базовой сама для себя."})
            if self.base_model.base_model_id:
                raise ValidationError(
                    {"base_model": "Базовой может быть только модель без размера."}
                )
            if self.pk and self.sizes.exists():
                raise ValidationError(
                    {"base_model": "У этой модели уже есть размеры — она сама базовая."}
                )

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = next_code("M")
        super().save(*args, **kwargs)

    def slot_names(self) -> list[str]:
        """Цветовые слоты модели в порядке появления в составе."""
        names: list[str] = []
        for line in self.model_parts.order_by("pk"):
            if line.slot and line.slot not in names:
                names.append(line.slot)
        return names

    def slot_default_materials(self) -> dict[str, Material]:
        """Материал по умолчанию для каждого слота — из первой детали слота."""
        result: dict[str, Material] = {}
        for line in self.model_parts.select_related("part__material__category").order_by("pk"):
            if line.slot and line.slot not in result:
                result[line.slot] = line.part.material
        return result

    def calculated_weight_g(self) -> Decimal | None:
        """
        Расчётная масса пластика: нормы расхода деталей × количество, только по материалам
        в граммах (листовые и покупные части не учитываются). None — нечего считать.
        """
        total, found = Decimal(0), False
        for line in self.model_parts.select_related("part__material"):
            if line.part.material.unit == Unit.GRAM:
                total += line.part.material_per_piece * line.quantity
                found = True
        return total if found else None


class ModelPart(models.Model):
    model = models.ForeignKey(ProductModel, on_delete=models.CASCADE, related_name="model_parts")
    part = models.ForeignKey(
        Part,
        verbose_name="Деталь",
        on_delete=models.PROTECT,
        related_name="model_lines",
    )
    quantity = models.PositiveIntegerField("Кол-во", default=1, validators=[MinValueValidator(1)])
    slot = models.CharField(
        "Цветовой слот",
        max_length=50,
        blank=True,
        help_text="Детали с одинаковым слотом в варианте получают один цвет. Пусто — цвет не меняется.",
    )

    class Meta:
        verbose_name = "Деталь в модели"
        verbose_name_plural = "Детали"

    def __str__(self):
        return f"{self.part} × {self.quantity}"

    def save(self, *args, **kwargs):
        self.slot = clean_slot(self.slot)
        super().save(*args, **kwargs)


class ModelComponent(models.Model):
    model = models.ForeignKey(ProductModel, on_delete=models.CASCADE, related_name="components")
    material = models.ForeignKey(
        Material,
        verbose_name="Комплектующее",
        on_delete=models.PROTECT,
        related_name="model_components",
        limit_choices_to={"category__kind__in": COMPONENT_KINDS},
    )
    quantity = positive_decimal_field("Кол-во на 1 шт")

    class Meta:
        verbose_name = "Покупное комплектующее"
        verbose_name_plural = "Покупные комплектующие"

    def __str__(self):
        return f"{self.material} × {self.quantity}"


class ModelOperation(models.Model):
    model = models.ForeignKey(ProductModel, on_delete=models.CASCADE, related_name="operations")
    operation_type = models.ForeignKey(
        OperationType,
        verbose_name="Операция",
        on_delete=models.PROTECT,
        limit_choices_to={"level": OperationLevel.MODEL},
    )
    minutes = _minutes_field()
    rate = _rate_field()

    class Meta:
        verbose_name = "Сборочная операция"
        verbose_name_plural = "Сборка"

    def __str__(self):
        return str(self.operation_type)


# --- Вариант ---------------------------------------------------------------------


class Coloring(models.TextChoices):
    FIXED = "fixed", "Задуманная"
    RANDOM = "random", "Случайная (ассорти)"


class Variant(ArchivableModel):
    """Конкретная расцветка модели (ТЗ п. 6.7)."""

    model = models.ForeignKey(
        ProductModel, verbose_name="Модель", on_delete=models.CASCADE, related_name="variants"
    )
    name = models.CharField("Название", max_length=100)
    name_key = models.CharField(max_length=100, editable=False)
    code = CodeField("Номер", max_length=15)
    coloring = models.CharField(
        "Тип расцветки", max_length=10, choices=Coloring.choices, default=Coloring.FIXED
    )
    card_warning = models.CharField(
        "Предупреждение для карточки",
        max_length=200,
        blank=True,
        help_text="Например: «цвета могут отличаться от фото».",
    )

    cost_cached = models.DecimalField(
        "Себестоимость, ₸", max_digits=14, decimal_places=4, null=True, editable=False
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "Вариант"
        verbose_name_plural = "Варианты"
        ordering = ["model__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["model", "code"], name="production_variant_code_uniq"),
            models.UniqueConstraint(
                fields=["model", "name_key"], name="production_variant_name_uniq"
            ),
        ]

    def __str__(self):
        return f"{self.model.name} — {self.name}"

    @property
    def full_code(self) -> str:
        """M-0000001_02"""
        return f"{self.model.code}_{self.code}"

    def clean(self):
        super().clean()
        self.name = clean_name(self.name)
        if self.model_id and self.name:
            qs = Variant.objects.filter(model_id=self.model_id, name_key=name_key(self.name))
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({"name": "У этой модели уже есть вариант с таким названием."})

    def save(self, *args, **kwargs):
        self.name = clean_name(self.name)
        self.name_key = name_key(self.name)
        if not self.code:
            numbers = [
                int(c)
                for c in Variant.objects.filter(model_id=self.model_id).values_list(
                    "code", flat=True
                )
                if c.isdigit()
            ]
            self.code = f"{max(numbers, default=0) + 1:02d}"
        super().save(*args, **kwargs)


class VariantSlot(models.Model):
    """Какой материал идёт в слот модели. Строки создаются автоматически."""

    variant = models.ForeignKey(Variant, on_delete=models.CASCADE, related_name="slots")
    slot = models.CharField("Слот", max_length=50)
    material = models.ForeignKey(
        Material,
        verbose_name="Материал",
        on_delete=models.PROTECT,
        related_name="variant_slots",
        limit_choices_to={"category__kind__in": PRODUCTION_KINDS},
        help_text="Материал с цветом, например «PLA Basic, зелёный». У «Ассорти» цвет не важен: "
        "берётся среднее по всем цветам этой линейки.",
    )

    class Meta:
        verbose_name = "Слот варианта"
        verbose_name_plural = "Цвета по слотам"
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(fields=["variant", "slot"], name="production_variantslot_uniq")
        ]

    def __str__(self):
        return f"{self.slot}: {self.material}"

    def clean(self):
        super().clean()
        if not (self.variant_id and self.material_id):
            return
        default = self.variant.model.slot_default_materials().get(self.slot)
        if default and self.material.category_id != default.category_id:
            raise ValidationError(
                {
                    "material": f"Для слота «{self.slot}» подходят только материалы категории "
                    f"«{default.category}»."
                }
            )
        if default and self.material.unit != default.unit:
            raise ValidationError(
                {
                    "material": f"Расход детали задан в «{default.get_unit_display()}», "
                    f"а у «{self.material}» единица — «{self.material.get_unit_display()}»."
                }
            )
