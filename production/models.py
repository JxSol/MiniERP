"""
Производство: детали, модели и их варианты (ТЗ, разделы 3 и 6.5–6.7).
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from simple_history.models import HistoricalRecords

from catalog.models import (
    COMPONENT_KINDS,
    PRODUCTION_KINDS,
    EnteredUnitMixin,
    Equipment,
    Material,
    MaterialKind,
    OperationLevel,
    OperationType,
    QtyUnit,
    Unit,
    categories_text,
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
    next_number,
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
    code = models.CharField(
        "Код для артикулов",
        max_length=3,
        blank=True,
        validators=[
            RegexValidator(
                r"^[A-HJ-NP-WYZ]{2,3}$", "2–3 латинские заглавные буквы; I, O и X не используются."
            )
        ],
        help_text="Задаётся у самой глубокой категории товара: TE, TF, HPB. Из него и номера "
        "модели складывается артикул (TE0001A). Буква X зарезервирована для наборов-миксов.",
    )

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
            ),
            models.UniqueConstraint(
                fields=["code"],
                condition=~models.Q(code=""),
                name="production_productcategory_code_uniq",
            ),
        ]

    def __str__(self):
        return self.path or self.name

    @property
    def article_code(self) -> str:
        """Код для артикулов: свой или ближайшего родителя."""
        node = self
        while node is not None:
            if node.code:
                return node.code
            node = node.parent
        return ""

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
    """
    То, что изготавливается на одном станке за один проход (ТЗ п. 6.5): чертёж без цвета.
    Из чего деталь делается — позиции материалов (PartMaterial): обычно одна, у композитной
    детали (печать несколькими пластиками через АМС) несколько. Расход и время указываются
    на типовую партию — так, как деталь обычно печатают: стол или лист из N штук.
    Конкретные материалы (цвета) появляются у варианта и при выпуске (PartColoring).
    """

    code = CodeField("Код", unique=True)
    equipment = models.ForeignKey(
        Equipment,
        verbose_name="Станок",
        on_delete=models.PROTECT,
        related_name="parts",
        help_text="Поменять технологию у готовой детали нельзя — это уже другая деталь: нажмите «Копировать деталь».",
    )
    batch_minutes = models.DecimalField(
        "Время типовой партии",
        max_digits=10,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    batch_size = models.PositiveIntegerField(
        "Штук в типовой партии",
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Сколько штук на одном столе или листе, как обычно печатаете.",
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

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = next_code(f"C-{self.equipment.technology_code}")
        super().save(*args, **kwargs)

    # --- материалы ---

    @property
    def positions(self) -> list[PartMaterial]:
        """Позиции материалов по порядку (использует prefetch, если он был)."""
        return list(self.materials.all())

    @property
    def main_position(self) -> PartMaterial | None:
        positions = self.positions
        return positions[0] if positions else None

    @property
    def material(self) -> Material | None:
        """Материал по умолчанию первой позиции — для списков и фильтров."""
        main = self.main_position
        return main.material if main else None

    @property
    def hours_per_piece(self) -> Decimal:
        return self.batch_minutes / 60 / self.batch_size

    def grams_per_piece(self) -> Decimal | None:
        """Пластик на одну штуку по всем позициям в граммах; None — нечего считать."""
        total, found = Decimal(0), False
        for position in self.positions:
            if position.material.unit == Unit.GRAM:
                total += position.per_piece
                found = True
        return total if found else None

    def slot_names(self) -> list[str]:
        names: list[str] = []
        for position in self.positions:
            if position.slot and position.slot not in names:
                names.append(position.slot)
        return names


class PartMaterial(models.Model):
    """
    Позиция материала детали: материал по умолчанию, расход на типовую партию и цветовая
    группа. Группа говорит, что цвет этой позиции выбирает вариант; пустая группа — цвет
    фиксирован (фанера, поддержки, всегда белые глаза).
    """

    part = models.ForeignKey(Part, on_delete=models.CASCADE, related_name="materials")
    order = models.PositiveSmallIntegerField("№", default=1)
    name = models.CharField(
        "Позиция",
        max_length=50,
        blank=True,
        help_text="Пусто у обычной детали; у композитной — «Корпус», «Глаза», «Узор».",
    )
    material = models.ForeignKey(
        Material,
        verbose_name="Материал по умолчанию",
        on_delete=models.PROTECT,
        related_name="part_positions",
        limit_choices_to={"category__kind__in": PRODUCTION_KINDS},
    )
    batch_qty = positive_decimal_field("Расход на типовую партию")
    batch_unit = models.CharField(
        "Единица расхода",
        max_length=4,
        choices=QtyUnit.choices,
        blank=True,
        default="",
    )
    slot = models.CharField(
        "Цветовая группа",
        max_length=50,
        blank=True,
        help_text="Пусто — цвет не меняется.",
    )

    class Meta:
        verbose_name = "Материал детали"
        verbose_name_plural = "Материалы"
        ordering = ["order", "pk"]

    def __str__(self):
        return f"{self.label}: {self.material}"

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        return "Материал" if self.order <= 1 else f"Материал {self.order}"

    @property
    def per_piece(self) -> Decimal:
        return self.batch_qty / self.part.batch_size

    def clean(self):
        super().clean()
        self.name = clean_name(self.name)
        self.slot = clean_slot(self.slot)
        try:
            part = self.part  # в форме детали — ещё не сохранённая деталь
        except Part.DoesNotExist:
            part = None
        if part is None:
            return
        if self.batch_qty and part.batch_size:
            if (self.batch_qty / part.batch_size).quantize(Decimal("0.0001")) <= 0:
                raise ValidationError(
                    {
                        "batch_qty": "Расход на одну штуку меньше 0,0001 — укажите "
                        "партию крупнее или материал в более мелкой единице."
                    }
                )
        if self.material_id and part.equipment_id:
            equipment = part.equipment
            if self.material.kind not in equipment.allowed_material_kinds:
                raise ValidationError(
                    {
                        "material": f"Материал «{self.material}» не подходит для станка "
                        f"«{equipment}» ({equipment.get_technology_display()})."
                    }
                )


class PartColoring(models.Model):
    """
    Расцветка детали: из какого материала сделана каждая позиция. Это и есть «цвет» детали
    на складе: «Плафон Луна (PLA Basic, белый)», «Фигурка (PLA чёрный + Глаза: PLA белый)».
    Создаётся системой при выпуске и при создании вариантов; руками не заводится.
    """

    part = models.ForeignKey(Part, on_delete=models.CASCADE, related_name="colorings")
    key = models.CharField(max_length=200, editable=False)
    label = models.CharField("Расцветка", max_length=300, editable=False)

    class Meta:
        verbose_name = "Расцветка детали"
        verbose_name_plural = "Расцветки деталей"
        ordering = ["part__name", "label"]
        constraints = [
            models.UniqueConstraint(fields=["part", "key"], name="production_partcoloring_uniq")
        ]

    def __str__(self):
        return f"{self.part} ({self.label})"

    def materials(self) -> dict[int, Material]:
        """{id позиции: материал}."""
        return {item.position_id: item.material for item in self.items.all()}

    @property
    def main_material(self) -> Material | None:
        items = list(self.items.all())
        return items[0].material if items else None

    @staticmethod
    def build_key(part: Part, materials: dict[int, Material]) -> str:
        return ";".join(f"{p.pk}:{materials[p.pk].pk}" for p in part.positions)

    @staticmethod
    def build_label(part: Part, materials: dict[int, Material]) -> str:
        parts = []
        for position in part.positions:
            name = materials[position.pk].full_name
            parts.append(f"{position.label}: {name}" if position.name else name)
        return " + ".join(parts)

    @classmethod
    def get_for(cls, part: Part, materials: dict[int, Material] | None = None) -> PartColoring:
        """Расцветка для набора материалов по позициям; не указанные позиции — по умолчанию."""
        materials = materials or {}
        resolved = {p.pk: materials.get(p.pk) or p.material for p in part.positions}
        key = cls.build_key(part, resolved)
        label = cls.build_label(part, resolved)
        coloring, created = cls.objects.get_or_create(part=part, key=key, defaults={"label": label})
        if created:
            PartColoringItem.objects.bulk_create(
                [
                    PartColoringItem(coloring=coloring, position=p, material=resolved[p.pk])
                    for p in part.positions
                ]
            )
        elif coloring.label != label:
            coloring.label = label
            coloring.save(update_fields=["label"])
        return coloring

    def refresh_label(self) -> None:
        label = self.build_label(self.part, self.materials())
        if label != self.label:
            self.label = label
            self.save(update_fields=["label"])


class PartColoringItem(models.Model):
    coloring = models.ForeignKey(PartColoring, on_delete=models.CASCADE, related_name="items")
    position = models.ForeignKey(
        PartMaterial, on_delete=models.PROTECT, related_name="coloring_items"
    )
    material = models.ForeignKey(Material, on_delete=models.PROTECT, related_name="+")

    class Meta:
        ordering = ["position__order", "position_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["coloring", "position"], name="production_partcoloringitem_uniq"
            )
        ]

    def __str__(self):
        return f"{self.position.label}: {self.material}"


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
    description = models.TextField("Описание", blank=True)
    default_packaging = models.ForeignKey(
        Material,
        verbose_name="Упаковка по умолчанию",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        limit_choices_to={"category__kind": MaterialKind.PACKAGING},
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
        help_text="Без упаковки, по весам. Рядом — расчётная масса пластика по нормам деталей, для сверки.",
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
    )

    article_number = models.PositiveIntegerField(
        "Номер модели для артикула",
        null=True,
        blank=True,
        editable=False,
        help_text="Порядковый номер внутри кода категории; присваивается один раз.",
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
        if not self.article_number and self.category_id:
            code = self.category.article_code
            if code:
                self.article_number = next_number(f"ART-{code}")
        super().save(*args, **kwargs)

    @property
    def article_base(self) -> str:
        """Основа артикула: код категории + номер модели (TE0001). Пусто — у категории нет кода."""
        if not self.article_number or not self.category_id:
            return ""
        code = self.category.article_code
        return f"{code}{self.article_number:04d}" if code else ""

    def slot_positions(self) -> dict[str, list[tuple[ModelPart, PartMaterial]]]:
        """{цветовая группа: [(строка состава, позиция детали)]} в порядке появления в составе."""
        result: dict[str, list[tuple[ModelPart, PartMaterial]]] = {}
        lines = self.model_parts.select_related("part").prefetch_related(
            "part__materials__material__category__interchangeable"
        )
        for line in lines.order_by("pk"):
            for position in line.part.positions:
                if position.slot:
                    result.setdefault(position.slot, []).append((line, position))
        return result

    def slot_names(self) -> list[str]:
        """Цветовые группы (слоты) модели: собираются из позиций её деталей."""
        return list(self.slot_positions())

    def slot_default_materials(self, positions=None) -> dict[str, Material]:
        """Материал по умолчанию для каждой группы — из первой позиции в ней."""
        if positions is None:
            positions = self.slot_positions()
        return {slot: pairs[0][1].material for slot, pairs in positions.items()}

    def slot_allowed_categories(self, positions=None) -> dict[str, set[int]]:
        """
        Из каких категорий вариант может взять материал группы: категория должна подходить
        каждой позиции группы — та же, что у материала по умолчанию, или взаимозаменяемая с ней.
        """
        if positions is None:
            positions = self.slot_positions()
        result = {}
        for slot, pairs in positions.items():
            allowed = set.intersection(
                *(position.material.category.compatible_ids() for _, position in pairs)
            )
            # категория материала по умолчанию подходит группе всегда
            result[slot] = allowed | {pairs[0][1].material.category_id}
        return result

    def calculated_weight_g(self) -> Decimal | None:
        """
        Расчётная масса пластика: нормы расхода деталей × количество, только по материалам
        в граммах (листовые и покупные части не учитываются). None — нечего считать.
        """
        total, found = Decimal(0), False
        lines = self.model_parts.select_related("part").prefetch_related(
            "part__materials__material"
        )
        for line in lines:
            grams = line.part.grams_per_piece()
            if grams is not None:
                total += grams * line.quantity
                found = True
        return total.quantize(Decimal("0.1")) if found else None


class ModelPart(models.Model):
    model = models.ForeignKey(ProductModel, on_delete=models.CASCADE, related_name="model_parts")
    part = models.ForeignKey(
        Part,
        verbose_name="Деталь",
        on_delete=models.PROTECT,
        related_name="model_lines",
    )
    quantity = models.PositiveIntegerField("Кол-во", default=1, validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = "Деталь в модели"
        verbose_name_plural = "Детали"

    def __str__(self):
        return f"{self.part} × {self.quantity}"


def slot_conflicts(parts: list[Part]) -> list[str]:
    """
    Материалы по умолчанию в одной цветовой группе должны подходить друг другу: одна единица
    расхода и одна категория либо взаимозаменяемые. Иначе вариант не сможет выбрать для группы
    один материал. Сверяется каждая пара: взаимозаменяемость не передаётся по цепочке.
    """
    seen: dict[str, list[tuple[Part, Material]]] = {}
    problems = []
    for part in parts:
        for position in part.positions:
            if not position.slot:
                continue
            material = position.material
            others = seen.setdefault(position.slot, [])
            for other_part, other in others:
                problem = _slot_mismatch(position.slot, other_part, other, part, material)
                if problem:
                    problems.append(problem)
                    break
            others.append((part, material))
    return problems


def _slot_mismatch(slot: str, first_part, first: Material, part, material: Material) -> str:
    """Почему два материала не могут стоять в одной цветовой группе; пусто — могут."""
    advice = "Дайте одной из них другую группу в карточке детали"
    if first.category_id not in material.category.compatible_ids():
        return (
            f"В группе «{slot}» детали из разных категорий: «{first_part}» "
            f"({first.category}) и «{part}» ({material.category}). {advice} или отметьте "
            "эти категории взаимозаменяемыми."
        )
    if first.unit != material.unit:
        return (
            f"В группе «{slot}» у деталей разные единицы расхода: «{first_part}» "
            f"({first.get_unit_display()}) и «{part}» ({material.get_unit_display()}). {advice}."
        )
    return ""


class ModelComponent(EnteredUnitMixin):
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
        return f"{self.material}: {self.quantity_display()}"


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


ARTICLE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWYZ"  # без I и O (похожи на цифры) и X (микс)


def variant_letters(number: int) -> str:
    """Номер варианта → буквы артикула: 1 → A, 23 → Z, 24 → AA (как столбцы в таблице)."""
    result = ""
    while number > 0:
        number, rem = divmod(number - 1, len(ARTICLE_LETTERS))
        result = ARTICLE_LETTERS[rem] + result
    return result


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
    def article_letters(self) -> str:
        """Буква варианта для артикула: 01 → A, 02 → B."""
        return variant_letters(int(self.code)) if self.code and self.code.isdigit() else ""

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
    """Какой материал идёт в цветовую группу (слот) модели. Строки создаются автоматически."""

    variant = models.ForeignKey(Variant, on_delete=models.CASCADE, related_name="slots")
    slot = models.CharField("Группа", max_length=50)
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
        verbose_name = "Цвет группы варианта"
        verbose_name_plural = "Цвета по группам"
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
        model = self.variant.model
        positions = model.slot_positions()
        default = model.slot_default_materials(positions).get(self.slot)
        if not default:
            return
        allowed = model.slot_allowed_categories(positions)[self.slot]
        if self.material.category_id not in allowed:
            raise ValidationError(
                {
                    "material": f"Для группы «{self.slot}» подходят только материалы "
                    f"{categories_text(allowed)}."
                }
            )
        if self.material.unit != default.unit:
            raise ValidationError(
                {
                    "material": f"Расход детали задан в «{default.get_unit_display()}», "
                    f"а у «{self.material}» единица — «{self.material.get_unit_display()}»."
                }
            )
