"""
Склад и журнал производства (ТЗ, раздел 13).

Четыре уровня остатков: материалы, детали (по материалу), изделия (варианты), товары.
Остатки складываются из движений (StockMove), которые создают документы при проведении.
Документы: приход, выпуск деталей, сборка, упаковка, перемещение, инвентаризация.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q, Sum
from django.utils import timezone
from simple_history.models import HistoricalRecords

from catalog.models import PRODUCTION_KINDS, Equipment, Material, MaterialOffer
from core.models import CodeField, UniqueNameModel, next_code, positive_decimal_field
from production.models import Coloring, Part, Variant
from sales.models import Product

ZERO = Decimal(0)

# Ключ позиции на складе: (вид, id записи, id материала для детали)
ItemKey = tuple[str, int, int | None]


# --- Склады -------------------------------------------------------------------------


class Warehouse(UniqueNameModel):
    is_default = models.BooleanField(
        "По умолчанию", default=False, help_text="Подставляется в новые документы."
    )
    is_showcase = models.BooleanField(
        "Витрина: образцы, не для продажи",
        default=False,
        help_text="Остатки этого склада не входят в «Доступно» и показываются в остатках отдельно "
        "как «На витрине». Образцы отправляются сюда документом «Перемещения».",
    )
    order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Склад"
        verbose_name_plural = "Склады"
        ordering = ["order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["name_key"], name="stock_warehouse_name_uniq")
        ]

    def clean(self):
        super().clean()
        if self.is_default and self.is_showcase:
            raise ValidationError(
                {"is_showcase": "Склад-витрина не может быть складом по умолчанию."}
            )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            Warehouse.objects.exclude(pk=self.pk).update(is_default=False)

    @classmethod
    def default(cls) -> Warehouse | None:
        return (
            cls.objects.filter(is_active=True, is_showcase=False)
            .order_by("-is_default", "order", "pk")
            .first()
        )


# --- Позиция склада ----------------------------------------------------------------


class ItemKind(models.TextChoices):
    MATERIAL = "material", "Материал"
    PART = "part", "Деталь"
    VARIANT = "variant", "Изделие"
    PRODUCT = "product", "Товар"


class StockItemMixin(models.Model):
    """Ровно одна из ссылок заполнена. У детали дополнительно указан материал (цвет)."""

    material = models.ForeignKey(
        Material,
        verbose_name="Материал",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    part = models.ForeignKey(
        Part,
        verbose_name="Деталь",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    part_material = models.ForeignKey(
        Material,
        verbose_name="Материал детали",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Из чего напечатана деталь (цвет).",
    )
    variant = models.ForeignKey(
        Variant,
        verbose_name="Изделие",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    product = models.ForeignKey(
        Product,
        verbose_name="Товар",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        abstract = True

    @property
    def kind(self) -> str:
        if self.material_id:
            return ItemKind.MATERIAL.value
        if self.part_id:
            return ItemKind.PART.value
        if self.variant_id:
            return ItemKind.VARIANT.value
        return ItemKind.PRODUCT.value

    @property
    def item_key(self) -> ItemKey:
        if self.material_id:
            return (ItemKind.MATERIAL.value, self.material_id, None)
        if self.part_id:
            return (ItemKind.PART.value, self.part_id, self.part_material_id)
        if self.variant_id:
            return (ItemKind.VARIANT.value, self.variant_id, None)
        return (ItemKind.PRODUCT.value, self.product_id, None)

    @property
    def item_label(self) -> str:
        if self.material_id:
            return str(self.material)
        if self.part_id:
            return (
                f"{self.part} ({self.part_material})" if self.part_material_id else str(self.part)
            )
        if self.variant_id:
            return str(self.variant)
        return f"{self.product.sku} {self.product.name}" if self.product_id else "—"

    @property
    def unit_label(self) -> str:
        return self.material.get_unit_display() if self.material_id else "шт"

    def clean_item(self):
        filled = [f for f in ("material", "part", "variant", "product") if getattr(self, f"{f}_id")]
        if len(filled) != 1:
            raise ValidationError(
                "Укажите ровно одну позицию: материал, деталь, изделие или товар."
            )
        if self.part_id and not self.part_material_id:
            raise ValidationError({"part_material": "Для детали укажите материал (цвет)."})
        if self.part_id and self.part_material_id:
            if self.part_material.category_id != self.part.material.category_id:
                raise ValidationError(
                    {
                        "part_material": f"Деталь «{self.part}» делается из «{self.part.material.category}»."
                    }
                )
        if not self.part_id and self.part_material_id:
            raise ValidationError({"part_material": "Материал детали указывается только у детали."})

    @staticmethod
    def key_filter(key: ItemKey) -> dict:
        kind, pk, material_id = key
        if kind == ItemKind.MATERIAL:
            return {"material_id": pk}
        if kind == ItemKind.PART:
            return {"part_id": pk, "part_material_id": material_id}
        if kind == ItemKind.VARIANT:
            return {"variant_id": pk}
        return {"product_id": pk}

    @staticmethod
    def key_label(key: ItemKey) -> str:
        kind, pk, material_id = key
        if kind == ItemKind.MATERIAL:
            return str(Material.objects.get(pk=pk))
        if kind == ItemKind.PART:
            return f"{Part.objects.get(pk=pk)} ({Material.objects.get(pk=material_id)})"
        if kind == ItemKind.VARIANT:
            return str(Variant.objects.get(pk=pk))
        return str(Product.objects.get(pk=pk))


def _item_constraint(name: str):
    one = (
        Q(material__isnull=False, part__isnull=True, variant__isnull=True, product__isnull=True)
        | Q(material__isnull=True, part__isnull=False, variant__isnull=True, product__isnull=True)
        | Q(material__isnull=True, part__isnull=True, variant__isnull=False, product__isnull=True)
        | Q(material__isnull=True, part__isnull=True, variant__isnull=True, product__isnull=False)
    )
    return models.CheckConstraint(condition=one, name=name)


# --- Движения и остатки ------------------------------------------------------------


class StockMove(StockItemMixin):
    """Одна проводка: плюс или минус по позиции на складе. Создаётся документом."""

    warehouse = models.ForeignKey(Warehouse, verbose_name="Склад", on_delete=models.PROTECT)
    date = models.DateField("Дата")
    quantity = models.DecimalField("Количество", max_digits=14, decimal_places=4)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    document = GenericForeignKey("content_type", "object_id")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Движение по складу"
        verbose_name_plural = "Движения по складу"
        ordering = ["-date", "-pk"]
        constraints = [_item_constraint("stock_move_single_item")]
        indexes = [
            models.Index(fields=["content_type", "object_id"]),
            models.Index(fields=["warehouse", "material"]),
            models.Index(fields=["warehouse", "part", "part_material"]),
            models.Index(fields=["warehouse", "variant"]),
            models.Index(fields=["warehouse", "product"]),
        ]

    def __str__(self):
        return f"{self.date} {self.item_label}: {self.quantity:+}"


class StockBalance(StockItemMixin):
    """Текущий остаток позиции на складе: сумма движений. Пересчитывается при проведении."""

    warehouse = models.ForeignKey(Warehouse, verbose_name="Склад", on_delete=models.PROTECT)
    quantity = models.DecimalField("Остаток", max_digits=14, decimal_places=4, default=ZERO)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Остаток"
        verbose_name_plural = "Остатки"
        ordering = ["material__name", "part__name", "variant__model__name", "product__name"]
        constraints = [
            _item_constraint("stock_balance_single_item"),
            models.UniqueConstraint(
                fields=["warehouse", "material", "part", "part_material", "variant", "product"],
                name="stock_balance_uniq",
                nulls_distinct=False,
            ),
        ]

    def __str__(self):
        return f"{self.item_label} @ {self.warehouse}: {self.quantity}"


def balance_of(warehouse_id: int, key: ItemKey, exclude_document=None) -> Decimal:
    """Остаток по движениям, при необходимости без движений указанного документа."""
    qs = StockMove.objects.filter(warehouse_id=warehouse_id, **StockItemMixin.key_filter(key))
    if exclude_document is not None:
        qs = qs.exclude(
            content_type=ContentType.objects.get_for_model(exclude_document),
            object_id=exclude_document.pk,
        )
    return qs.aggregate(total=Sum("quantity"))["total"] or ZERO


def total_of(key: ItemKey) -> Decimal:
    """Остаток по всем складам."""
    qs = StockMove.objects.filter(**StockItemMixin.key_filter(key))
    return qs.aggregate(total=Sum("quantity"))["total"] or ZERO


def recalc_balances(keys: set[tuple[int, ItemKey]]) -> None:
    for warehouse_id, key in keys:
        total = balance_of(warehouse_id, key)
        lookup = {"warehouse_id": warehouse_id, **StockItemMixin.key_filter(key)}
        for field in ("material_id", "part_id", "part_material_id", "variant_id", "product_id"):
            lookup.setdefault(field, None)
        if total == ZERO:
            StockBalance.objects.filter(**lookup).delete()
        else:
            StockBalance.objects.update_or_create(**lookup, defaults={"quantity": total})


# --- Документы ---------------------------------------------------------------------


class DocumentStatus(models.TextChoices):
    DRAFT = "draft", "Черновик"
    POSTED = "posted", "Проведён"
    CANCELLED = "cancelled", "Отменён"


class Shortage(ValidationError):
    """Остаток ушёл бы в минус."""


class StockDocument(models.Model):
    """
    Общее для всех складских документов. Документ проводится методом post(): его прежние
    движения удаляются и создаются заново, остатки проверяются на минус и пересчитываются.
    """

    number_prefix = ""  # BUY, MAKE, ASM, PACK, MOVE, INV

    number = CodeField("Номер", unique=True)
    date = models.DateField("Дата", default=timezone.localdate)
    warehouse = models.ForeignKey(
        Warehouse, verbose_name="Склад", on_delete=models.PROTECT, help_text="Где это произошло."
    )
    status = models.CharField(
        "Статус", max_length=10, choices=DocumentStatus.choices, default=DocumentStatus.DRAFT
    )
    notes = models.TextField("Примечание", blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Кто записал",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        editable=False,
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Изменено", auto_now=True)
    posted_at = models.DateTimeField("Проведено", null=True, blank=True, editable=False)

    class Meta:
        abstract = True
        ordering = ["-date", "-pk"]

    def __str__(self):
        return self.number or f"{self._meta.verbose_name} (новый документ)"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = next_code(self.number_prefix)
        super().save(*args, **kwargs)

    # --- что проводим ---

    def build_moves(self) -> list[tuple[int, ItemKey, Decimal]]:
        """Список (склад, позиция, количество со знаком). Переопределяется в документах."""
        raise NotImplementedError

    def after_post(self) -> None:
        """Побочные действия после проведения (например, обновить цену предложения)."""

    # --- проведение ---

    def _moves_qs(self):
        return StockMove.objects.filter(
            content_type=ContentType.objects.get_for_model(self), object_id=self.pk
        )

    def check_shortages(self, moves) -> list[str]:
        """Какие позиции ушли бы в минус после проведения этих движений."""
        deltas: dict[tuple[int, ItemKey], Decimal] = defaultdict(lambda: ZERO)
        for warehouse_id, key, qty in moves:
            deltas[(warehouse_id, key)] += qty
        shortages = []
        for (warehouse_id, key), delta in deltas.items():
            if delta >= 0:
                continue
            have = balance_of(warehouse_id, key, exclude_document=self)
            if have + delta < 0:
                unit = (
                    Material.objects.get(pk=key[1]).get_unit_display()
                    if key[0] == "material"
                    else "шт"
                )
                warehouse = Warehouse.objects.get(pk=warehouse_id)
                shortages.append(
                    f"{StockItemMixin.key_label(key)} на складе «{warehouse}»: "
                    f"есть {have.normalize():f} {unit}, нужно {(-delta).normalize():f} {unit}"
                )
        return shortages

    @transaction.atomic
    def post(self) -> None:
        moves = self.build_moves()
        shortages = self.check_shortages(moves)
        if shortages:
            raise Shortage("Не хватает на складе: " + "; ".join(shortages) + ".")
        affected = {(m.warehouse_id, m.item_key) for m in self._moves_qs()}
        self._moves_qs().delete()
        content_type = ContentType.objects.get_for_model(self)
        for warehouse_id, key, qty in moves:
            if qty == 0:
                continue
            StockMove.objects.create(
                warehouse_id=warehouse_id,
                date=self.date,
                quantity=qty,
                content_type=content_type,
                object_id=self.pk,
                **StockItemMixin.key_filter(key),
            )
            affected.add((warehouse_id, key))
        self.status = DocumentStatus.POSTED
        self.posted_at = timezone.now()
        type(self).objects.filter(pk=self.pk).update(status=self.status, posted_at=self.posted_at)
        recalc_balances(affected)
        self.after_post()

    @transaction.atomic
    def cancel(self) -> None:
        affected = {(m.warehouse_id, m.item_key) for m in self._moves_qs()}
        shortages = []
        for warehouse_id, key in affected:
            have = balance_of(warehouse_id, key, exclude_document=self)
            if have < 0:
                shortages.append(
                    f"{StockItemMixin.key_label(key)} на складе "
                    f"«{Warehouse.objects.get(pk=warehouse_id)}»: не хватит {(-have).normalize():f}"
                )
        if shortages:
            raise Shortage("Отменить нельзя — остаток уйдёт в минус: " + "; ".join(shortages) + ".")
        self._moves_qs().delete()
        self.status = DocumentStatus.CANCELLED
        type(self).objects.filter(pk=self.pk).update(status=self.status)
        recalc_balances(affected)

    @property
    def is_cancelled(self) -> bool:
        return self.status == DocumentStatus.CANCELLED


# --- Приход ------------------------------------------------------------------------


class Receipt(StockDocument):
    """Закупка материалов у поставщиков."""

    number_prefix = "BUY"
    history = HistoricalRecords()

    class Meta(StockDocument.Meta):
        verbose_name = "Закупка"
        verbose_name_plural = "Закупки"

    def build_moves(self):
        return [
            (self.warehouse_id, (ItemKind.MATERIAL.value, line.material_id, None), line.quantity)
            for line in self.lines.all()
        ]

    def after_post(self):
        """Фактическая цена закупки попадает в предложение поставщика."""
        for line in self.lines.select_related("offer"):
            offer = line.offer
            if offer is None:
                continue
            if offer.pack_price != line.pack_price or offer.pack_size != line.pack_size:
                offer.pack_price = line.pack_price
                offer.pack_size = line.pack_size
                offer._changed_by = self.created_by
                offer.save()


class ReceiptLine(models.Model):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="lines")
    material = models.ForeignKey(
        Material, verbose_name="Материал", on_delete=models.PROTECT, related_name="receipt_lines"
    )
    offer = models.ForeignKey(
        MaterialOffer,
        verbose_name="Предложение поставщика",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="receipt_lines",
        help_text="У кого купили. Цена из этой строки обновит предложение.",
    )
    packs = positive_decimal_field("Упаковок", help_text="Сколько катушек, листов, пачек.")
    pack_size = positive_decimal_field(
        "В упаковке", help_text="Единиц расхода в одной упаковке. Подставляется из предложения."
    )
    pack_price = models.DecimalField(
        "Цена упаковки, ₸", max_digits=14, decimal_places=2, validators=[MinValueValidator(ZERO)]
    )

    class Meta:
        verbose_name = "Строка прихода"
        verbose_name_plural = "Позиции закупки"

    def __str__(self):
        return f"{self.material} × {self.packs}"

    @property
    def quantity(self) -> Decimal:
        return self.packs * self.pack_size

    @property
    def total(self) -> Decimal:
        return self.packs * self.pack_price

    def clean(self):
        super().clean()
        if self.offer_id and self.material_id and self.offer.material_id != self.material_id:
            raise ValidationError({"offer": "Предложение относится к другому материалу."})


# --- Выпуск деталей ----------------------------------------------------------------


class ProductionRun(StockDocument):
    """Выпуск деталей: напечатали или вырезали партию одной детали."""

    number_prefix = "MAKE"

    part = models.ForeignKey(
        Part, verbose_name="Деталь", on_delete=models.PROTECT, related_name="runs"
    )
    material = models.ForeignKey(
        Material,
        verbose_name="Материал",
        on_delete=models.PROTECT,
        related_name="runs",
        help_text="Из чего печатали (цвет). По умолчанию — материал детали.",
    )
    equipment = models.ForeignKey(
        Equipment,
        verbose_name="Станок",
        on_delete=models.PROTECT,
        related_name="runs",
        help_text="По умолчанию — станок детали.",
    )
    good_qty = models.PositiveIntegerField("Годных, шт", default=0)
    scrap_qty = models.PositiveIntegerField(
        "Брак, шт", default=0, help_text="Материал на брак тоже списывается."
    )
    history = HistoricalRecords()

    class Meta(StockDocument.Meta):
        verbose_name = "Выпуск деталей"
        verbose_name_plural = "Выпуск деталей"

    def clean_fields(self, exclude=None):
        # Умолчания из детали подставляются до проверки обязательных полей
        if self.part_id:
            if not self.material_id:
                self.material = self.part.material
            if not self.equipment_id:
                self.equipment = self.part.equipment
        super().clean_fields(exclude)

    def clean(self):
        super().clean()
        if self.part_id and self.material_id:
            if self.material.category_id != self.part.material.category_id:
                raise ValidationError(
                    {
                        "material": f"Деталь «{self.part}» делается из «{self.part.material.category}»."
                    }
                )
            if self.material.unit != self.part.material.unit:
                raise ValidationError({"material": "У материала другая единица расхода."})
        if (self.good_qty or 0) + (self.scrap_qty or 0) < 1:
            raise ValidationError({"good_qty": "Укажите, сколько получилось (годных или брака)."})
        if self.warehouse_id and self.part_id and self.material_id:
            shortages = self.check_shortages(self.build_moves())
            if shortages:
                raise ValidationError({"material": "Не хватает: " + "; ".join(shortages)})

    @property
    def material_used(self) -> Decimal:
        return self.part.material_per_piece * (self.good_qty + self.scrap_qty)

    def build_moves(self):
        moves = [
            (
                self.warehouse_id,
                (ItemKind.MATERIAL.value, self.material_id, None),
                -self.material_used,
            )
        ]
        if self.good_qty:
            moves.append(
                (
                    self.warehouse_id,
                    (ItemKind.PART.value, self.part_id, self.material_id),
                    Decimal(self.good_qty),
                )
            )
        return moves


# --- Сборка ------------------------------------------------------------------------


def _line_materials(material: Material) -> list[Material]:
    """Все цвета той же линейки и категории — для «Ассорти»."""
    return list(
        Material.objects.filter(
            category_id=material.category_id, name_key=material.name_key, is_active=True
        )
    )


class Assembly(StockDocument):
    """Сборка изделий (вариантов) из деталей и покупных комплектующих."""

    number_prefix = "ASM"

    variant = models.ForeignKey(
        Variant, verbose_name="Изделие", on_delete=models.PROTECT, related_name="assemblies"
    )
    quantity = models.PositiveIntegerField(
        "Сколько, шт", default=1, validators=[MinValueValidator(1)]
    )
    history = HistoricalRecords()

    class Meta(StockDocument.Meta):
        verbose_name = "Сборка изделий"
        verbose_name_plural = "Сборка изделий"

    def clean(self):
        super().clean()
        if self.warehouse_id and self.variant_id and self.quantity:
            shortages = self.check_shortages(self.build_moves())
            if shortages:
                raise ValidationError({"quantity": "Не хватает: " + "; ".join(shortages)})

    def _consume_parts(self) -> list[tuple[int, ItemKey, Decimal]]:
        model = self.variant.model
        slot_materials = {s.slot: s.material for s in self.variant.slots.select_related("material")}
        moves = []
        for line in model.model_parts.select_related("part__material"):
            need = Decimal(line.quantity * self.quantity)
            material = slot_materials.get(line.slot) if line.slot else None
            material = material or line.part.material
            if self.variant.coloring == Coloring.RANDOM and line.slot:
                # Ассорти: любой цвет линейки, сначала тот, которого больше
                candidates = _line_materials(material)
                stock = {
                    m.pk: balance_of(
                        self.warehouse_id, (ItemKind.PART.value, line.part_id, m.pk), self
                    )
                    for m in candidates
                }
                for m in sorted(candidates, key=lambda m: -stock[m.pk]):
                    if need <= 0:
                        break
                    take = min(need, max(stock[m.pk], ZERO))
                    if take > 0:
                        moves.append(
                            (self.warehouse_id, (ItemKind.PART.value, line.part_id, m.pk), -take)
                        )
                        need -= take
                if need > 0:  # не хватило — спишем с основного, проверка на минус покажет
                    moves.append(
                        (self.warehouse_id, (ItemKind.PART.value, line.part_id, material.pk), -need)
                    )
            else:
                moves.append(
                    (self.warehouse_id, (ItemKind.PART.value, line.part_id, material.pk), -need)
                )
        return moves

    def build_moves(self):
        moves = self._consume_parts()
        for comp in self.variant.model.components.all():
            moves.append(
                (
                    self.warehouse_id,
                    (ItemKind.MATERIAL.value, comp.material_id, None),
                    -(comp.quantity * self.quantity),
                )
            )
        moves.append(
            (
                self.warehouse_id,
                (ItemKind.VARIANT.value, self.variant_id, None),
                Decimal(self.quantity),
            )
        )
        return moves


# --- Упаковка ----------------------------------------------------------------------


class Packing(StockDocument):
    """Упаковка изделий в товар (поштучный или набор)."""

    number_prefix = "PACK"

    product = models.ForeignKey(
        Product, verbose_name="Товар", on_delete=models.PROTECT, related_name="packings"
    )
    quantity = models.PositiveIntegerField(
        "Сколько, шт", default=1, validators=[MinValueValidator(1)]
    )
    history = HistoricalRecords()

    class Meta(StockDocument.Meta):
        verbose_name = "Упаковка товаров"
        verbose_name_plural = "Упаковка товаров"

    def clean(self):
        super().clean()
        if self.warehouse_id and self.product_id and self.quantity:
            shortages = self.check_shortages(self.build_moves())
            if shortages:
                raise ValidationError({"quantity": "Не хватает: " + "; ".join(shortages)})

    def build_moves(self):
        moves = []
        for item in self.product.items.all():
            moves.append(
                (
                    self.warehouse_id,
                    (ItemKind.VARIANT.value, item.variant_id, None),
                    -Decimal(item.quantity * self.quantity),
                )
            )
        for pack in self.product.packaging.all():
            moves.append(
                (
                    self.warehouse_id,
                    (ItemKind.MATERIAL.value, pack.material_id, None),
                    -(pack.quantity * self.quantity),
                )
            )
        moves.append(
            (
                self.warehouse_id,
                (ItemKind.PRODUCT.value, self.product_id, None),
                Decimal(self.quantity),
            )
        )
        return moves


# --- Перемещение -------------------------------------------------------------------


class Transfer(StockDocument):
    """Перемещение позиций с одного склада на другой."""

    number_prefix = "MOVE"

    to_warehouse = models.ForeignKey(
        Warehouse, verbose_name="Куда", on_delete=models.PROTECT, related_name="+"
    )
    history = HistoricalRecords()

    class Meta(StockDocument.Meta):
        verbose_name = "Перемещение"
        verbose_name_plural = "Перемещения"

    def clean(self):
        super().clean()
        if self.warehouse_id and self.to_warehouse_id == self.warehouse_id:
            raise ValidationError({"to_warehouse": "Склады «откуда» и «куда» совпадают."})

    def build_moves(self):
        moves = []
        for line in self.lines.all():
            moves.append((self.warehouse_id, line.item_key, -line.quantity))
            moves.append((self.to_warehouse_id, line.item_key, line.quantity))
        return moves


class TransferLine(StockItemMixin):
    transfer = models.ForeignKey(Transfer, on_delete=models.CASCADE, related_name="lines")
    quantity = positive_decimal_field("Сколько")

    class Meta:
        verbose_name = "Строка перемещения"
        verbose_name_plural = "Позиции перемещения"
        constraints = [_item_constraint("stock_transferline_single_item")]

    def __str__(self):
        return f"{self.item_label} × {self.quantity}"

    def clean(self):
        super().clean()
        self.clean_item()


# --- Инвентаризация ----------------------------------------------------------------


class Inventory(StockDocument):
    """Инвентаризация: фактические остатки; разница записывается как корректировка."""

    number_prefix = "INV"
    history = HistoricalRecords()

    class Meta(StockDocument.Meta):
        verbose_name = "Инвентаризация"
        verbose_name_plural = "Инвентаризации"

    def build_moves(self):
        moves = []
        for line in self.lines.all():
            have = balance_of(self.warehouse_id, line.item_key, exclude_document=self)
            diff = line.counted_qty - have
            line.difference = diff
            type(line).objects.filter(pk=line.pk).update(difference=diff)
            if diff != 0:
                moves.append((self.warehouse_id, line.item_key, diff))
        return moves


class InventoryLine(StockItemMixin):
    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE, related_name="lines")
    counted_qty = models.DecimalField(
        "Фактический остаток",
        max_digits=14,
        decimal_places=4,
        validators=[MinValueValidator(ZERO)],
        help_text="Сколько есть на самом деле. Разница с учётным остатком запишется автоматически.",
    )
    difference = models.DecimalField(
        "Разница", max_digits=14, decimal_places=4, null=True, blank=True, editable=False
    )
    reason = models.CharField(
        "Причина",
        max_length=200,
        blank=True,
        help_text="Например: взвесили катушку, начальный остаток.",
    )

    class Meta:
        verbose_name = "Строка инвентаризации"
        verbose_name_plural = "Позиции инвентаризации"
        constraints = [_item_constraint("stock_inventoryline_single_item")]

    def __str__(self):
        return f"{self.item_label}: {self.counted_qty}"

    def clean(self):
        super().clean()
        self.clean_item()


# --- Оприходование и списание ------------------------------------------------------


class ReasonDirection(models.TextChoices):
    IN = "in", "Оприходование"
    OUT = "out", "Списание"
    BOTH = "both", "Оприходование и списание"


class StockReason(UniqueNameModel):
    """Причина оприходования или списания — справочник, пользователи выбирают из готового."""

    direction = models.CharField(
        "Где используется",
        max_length=5,
        choices=ReasonDirection.choices,
        default=ReasonDirection.BOTH,
    )
    order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Причина прихода / списания"
        verbose_name_plural = "Причины прихода и списания"
        ordering = ["order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["name_key"], name="stock_stockreason_name_uniq")
        ]

    @classmethod
    def for_direction(cls, direction: str):
        return cls.objects.filter(direction__in=[direction, ReasonDirection.BOTH])


class AdjustmentDocument(StockDocument):
    """
    Общее для оприходования и списания: причина из справочника и позиции любого уровня.
    Приход — начальные остатки, покупка готовых деталей, возврат; списание — брак на складе,
    образцы, порча. Закупки материалов и производство здесь не оформляются.
    """

    direction = ""  # ReasonDirection.IN или OUT — задаётся в наследниках
    sign = 1

    reason = models.ForeignKey(
        StockReason,
        verbose_name="Причина",
        on_delete=models.PROTECT,
        related_name="+",
        help_text="Из справочника «Причины прихода и списания».",
    )

    class Meta(StockDocument.Meta):
        abstract = True

    def clean(self):
        super().clean()
        if self.reason_id and self.reason.direction not in (self.direction, ReasonDirection.BOTH):
            raise ValidationError(
                {
                    "reason": f"Причина «{self.reason}» не подходит для документа «{self._meta.verbose_name}»."
                }
            )

    def build_moves(self):
        return [
            (self.warehouse_id, line.item_key, self.sign * line.quantity)
            for line in self.lines.all()
        ]


class Receiving(AdjustmentDocument):
    """Оприходование: позиции появляются на складе без закупки и производства."""

    number_prefix = "RCV"
    direction = ReasonDirection.IN
    sign = 1
    history = HistoricalRecords()

    class Meta(StockDocument.Meta):
        verbose_name = "Оприходование"
        verbose_name_plural = "Оприходования"


class WriteOff(AdjustmentDocument):
    """Списание: позиции уходят со склада не в производство и не в продажу."""

    number_prefix = "WRO"
    direction = ReasonDirection.OUT
    sign = -1
    history = HistoricalRecords()

    class Meta(StockDocument.Meta):
        verbose_name = "Списание"
        verbose_name_plural = "Списания"


class AdjustmentLine(StockItemMixin):
    quantity = positive_decimal_field("Сколько")

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.item_label} × {self.quantity}"

    def clean(self):
        super().clean()
        self.clean_item()


class ReceivingLine(AdjustmentLine):
    document = models.ForeignKey(Receiving, on_delete=models.CASCADE, related_name="lines")

    class Meta:
        verbose_name = "Строка оприходования"
        verbose_name_plural = "Позиции оприходования"
        constraints = [_item_constraint("stock_receivingline_single_item")]


class WriteOffLine(AdjustmentLine):
    document = models.ForeignKey(WriteOff, on_delete=models.CASCADE, related_name="lines")

    class Meta:
        verbose_name = "Строка списания"
        verbose_name_plural = "Позиции списания"
        constraints = [_item_constraint("stock_writeoffline_single_item")]


DOCUMENT_MODELS = (
    Receipt,
    ProductionRun,
    Assembly,
    Packing,
    Transfer,
    Inventory,
    Receiving,
    WriteOff,
)


def material_kind_ok(material: Material) -> bool:
    return material.kind in PRODUCTION_KINDS
