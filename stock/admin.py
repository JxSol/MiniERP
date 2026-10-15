"""
Экраны склада: записи (купили, напечатали, собрали, упаковали, переместили,
пересчитали), остатки и журнал движений.
"""

from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin
from unfold.decorators import action, display
from unfold.widgets import UnfoldAdminSelectWidget

from core.admin import ArchiveAdmin, HistoryAdmin, LineInline
from core.admin_utils import fmt_number, hx_redirect
from core.forms import ActiveChoicesForm

from . import reports
from .models import (
    Assembly,
    DocumentStatus,
    Inventory,
    InventoryLine,
    ItemKind,
    Packing,
    ProductionRun,
    Receipt,
    ReceiptLine,
    Shortage,
    StockBalance,
    StockMove,
    Transfer,
    TransferLine,
    Warehouse,
)

ITEM_FIELDS = ("material", "part", "part_material", "variant", "product")


def fmt_qty(value, unit="шт") -> str:
    """2000 → «2 000 г», 12.5 → «12,5 шт»."""
    if value is None:
        return "—"
    text = f"{Decimal(value).normalize():,f}".replace(",", " ").replace(".", ",")
    return f"{text} {unit}"


def _doc_link(move: StockMove) -> str:
    doc = move.document
    if doc is None:
        return "—"
    url = reverse(f"admin:{doc._meta.app_label}_{doc._meta.model_name}_change", args=[doc.pk])
    return format_html('<a href="{}" class="text-primary-600">{}</a>', url, doc.number)


# --- Склады ------------------------------------------------------------------------


@admin.register(Warehouse)
class WarehouseAdmin(ArchiveAdmin):
    list_display = ("name", "is_default", "order", "is_active")
    list_editable = ("order",)
    search_fields = ("name",)
    fields = ("name", "is_default", "order", "is_active", "notes")


# --- Общее для записей -------------------------------------------------------------


class DocumentForm(ActiveChoicesForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "warehouse" in self.fields and not self.instance.pk:
            default = Warehouse.default()
            if default:
                self.fields["warehouse"].initial = default.pk


class DocumentAdmin(HistoryAdmin, ModelAdmin):
    """Запись проводится при сохранении; отменить можно кнопкой вверху."""

    form = DocumentForm
    list_filter = ("status", "warehouse")
    readonly_fields = ("number", "status_badge", "posted_at", "created_by")
    actions_detail = ("cancel_document",)
    warn_unsaved_form = True
    list_filter_submit = False

    @display(
        description="Статус",
        label={
            DocumentStatus.DRAFT: "warning",
            DocumentStatus.POSTED: "success",
            DocumentStatus.CANCELLED: "danger",
        },
    )
    def status_badge(self, obj):
        return obj.status, obj.get_status_display()

    def has_change_permission(self, request, obj=None):
        if obj is not None and not isinstance(obj, str) and obj.is_cancelled:
            return False
        return super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        doc = form.instance
        try:
            doc.post()
        except Shortage as error:
            messages.error(
                request,
                "Запись сохранена как черновик и НЕ проведена. " + " ".join(error.messages),
            )
        else:
            messages.success(request, f"{doc.number} проведён, остатки обновлены.")

    def has_cancel_permission(self, request, object_id=None):
        opts = self.model._meta
        return request.user.has_perm(f"{opts.app_label}.change_{opts.model_name}")

    @action(
        description="Отменить запись",
        icon="undo",
        url_path="cancel",
        permissions=["cancel"],
        dialog={
            "title": "Отменить запись",
            "description": "Движения по складу будут удалены, остатки пересчитаны. "
            "Запись останется в истории как отменённая.",
            "form_submit_text": "Отменить запись",
        },
    )
    def cancel_document(self, request, form, object_id):
        doc = get_object_or_404(self.model, pk=object_id)
        try:
            doc.cancel()
        except Shortage as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(request, f"{doc.number} отменён.")
        opts = self.model._meta
        return hx_redirect(
            request, reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[object_id])
        )


# --- Приход ------------------------------------------------------------------------


class ReceiptLineForm(ActiveChoicesForm):
    class Meta:
        model = ReceiptLine
        fields = ("material", "offer", "packs", "pack_size", "pack_price")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pack_size"].required = False
        self.fields["pack_price"].required = False
        self.fields["offer"].queryset = self.fields["offer"].queryset.select_related(
            "material__color", "manufacturer", "supplier"
        )

    def clean(self):
        data = super().clean()
        offer = data.get("offer")
        if offer and not data.get("pack_size"):
            data["pack_size"] = offer.pack_size
        if offer and data.get("pack_price") is None:
            data["pack_price"] = offer.pack_price
        if not data.get("pack_size"):
            self.add_error(
                "pack_size", "Укажите, сколько единиц в упаковке, или выберите предложение."
            )
        if data.get("pack_price") is None:
            self.add_error("pack_price", "Укажите цену упаковки или выберите предложение.")
        return data


class ReceiptLineInline(LineInline):
    model = ReceiptLine
    form = ReceiptLineForm
    autocomplete_fields = ("material",)
    fields = (
        "material",
        "offer",
        "packs",
        "pack_size",
        "pack_price",
        "quantity_display",
        "total_display",
    )
    readonly_fields = ("quantity_display", "total_display")
    min_num = 1
    verbose_name_plural = "Что купили"

    @display(description="Итого единиц")
    def quantity_display(self, obj):
        return fmt_qty(obj.quantity, obj.material.get_unit_display()) if obj.pk else "—"

    @display(description="Сумма, ₸")
    def total_display(self, obj):
        return fmt_number(obj.total) if obj.pk else "—"


@admin.register(Receipt)
class ReceiptAdmin(DocumentAdmin):
    list_display = ("number", "date", "warehouse", "lines_display", "status_badge", "created_by")
    search_fields = ("number", "lines__material__name")
    inlines = (ReceiptLineInline,)
    fields = ("number", "date", "warehouse", "status_badge", "notes", "created_by", "posted_at")

    @display(description="Что купили")
    def lines_display(self, obj):
        return "; ".join(f"{line.material} × {fmt_number(line.packs)}" for line in obj.lines.all())


# --- Выпуск деталей ----------------------------------------------------------------


class ProductionRunForm(DocumentForm):
    class Meta:
        model = ProductionRun
        fields = (
            "date",
            "warehouse",
            "part",
            "material",
            "equipment",
            "good_qty",
            "scrap_qty",
            "notes",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["material"].required = False
        self.fields["material"].help_text = "Пусто — материал детали по умолчанию."
        self.fields["equipment"].required = False
        self.fields["equipment"].help_text = "Пусто — станок детали."

    def clean(self):
        data = super().clean()
        part = data.get("part")
        if part:
            data.setdefault("material", None)
            data.setdefault("equipment", None)
            if not data["material"]:
                data["material"] = part.material
            if not data["equipment"]:
                data["equipment"] = part.equipment
        return data


@admin.register(ProductionRun)
class ProductionRunAdmin(DocumentAdmin):
    form = ProductionRunForm
    list_display = (
        "number",
        "date",
        "part",
        "material",
        "equipment",
        "good_qty",
        "scrap_qty",
        "status_badge",
    )
    list_filter = ("status", "warehouse", "equipment", "part")
    search_fields = ("number", "part__name", "material__name")
    autocomplete_fields = ("part", "material", "equipment")
    fields = (
        "number",
        "date",
        "warehouse",
        "part",
        "material",
        "equipment",
        ("good_qty", "scrap_qty"),
        "status_badge",
        "notes",
        "created_by",
        "posted_at",
    )


# --- Сборка и упаковка -------------------------------------------------------------


@admin.register(Assembly)
class AssemblyAdmin(DocumentAdmin):
    list_display = ("number", "date", "variant", "quantity", "warehouse", "status_badge")
    list_filter = ("status", "warehouse", "variant__model")
    search_fields = ("number", "variant__name", "variant__model__name")
    autocomplete_fields = ("variant",)
    fields = (
        "number",
        "date",
        "warehouse",
        "variant",
        "quantity",
        "status_badge",
        "notes",
        "created_by",
        "posted_at",
    )


@admin.register(Packing)
class PackingAdmin(DocumentAdmin):
    list_display = ("number", "date", "product", "quantity", "warehouse", "status_badge")
    list_filter = ("status", "warehouse")
    search_fields = ("number", "product__name", "product__sku")
    autocomplete_fields = ("product",)
    fields = (
        "number",
        "date",
        "warehouse",
        "product",
        "quantity",
        "status_badge",
        "notes",
        "created_by",
        "posted_at",
    )


# --- Строки с выбором позиции ------------------------------------------------------


class ItemLineForm(ActiveChoicesForm):
    """Строка с позицией: материал, деталь + материал, изделие или товар."""

    def clean(self):
        data = super().clean()
        filled = [f for f in ("material", "part", "variant", "product") if data.get(f)]
        if len(filled) != 1:
            raise forms.ValidationError(
                "В строке укажите ровно одну позицию: материал, деталь, изделие или товар."
            )
        if data.get("part") and not data.get("part_material"):
            self.add_error("part_material", "Для детали укажите материал (цвет).")
        return data


class TransferLineInline(LineInline):
    model = TransferLine
    form = ItemLineForm
    autocomplete_fields = ITEM_FIELDS
    fields = (*ITEM_FIELDS, "quantity")
    min_num = 1
    verbose_name_plural = "Что переместили"


class InventoryLineInline(LineInline):
    model = InventoryLine
    form = ItemLineForm
    autocomplete_fields = ITEM_FIELDS
    fields = (*ITEM_FIELDS, "counted_qty", "difference", "reason")
    readonly_fields = ("difference",)
    min_num = 1
    verbose_name_plural = "Что пересчитали"


@admin.register(Transfer)
class TransferAdmin(DocumentAdmin):
    list_display = ("number", "date", "warehouse", "to_warehouse", "status_badge", "created_by")
    inlines = (TransferLineInline,)
    search_fields = ("number",)
    fields = (
        "number",
        "date",
        ("warehouse", "to_warehouse"),
        "status_badge",
        "notes",
        "created_by",
        "posted_at",
    )

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["warehouse"].label = "Откуда"
        return form


@admin.register(Inventory)
class InventoryAdmin(DocumentAdmin):
    list_display = ("number", "date", "warehouse", "lines_count", "status_badge", "created_by")
    inlines = (InventoryLineInline,)
    search_fields = ("number",)
    fields = ("number", "date", "warehouse", "status_badge", "notes", "created_by", "posted_at")

    @display(description="Позиций")
    def lines_count(self, obj):
        return obj.lines.count()


# --- Остатки и движения ------------------------------------------------------------


class KindFilter(SimpleListFilter):
    title = "Уровень"
    parameter_name = "kind"

    def lookups(self, request, model_admin):
        return ItemKind.choices

    def queryset(self, request, queryset):
        value = self.value()
        if value == ItemKind.MATERIAL:
            return queryset.filter(material__isnull=False)
        if value == ItemKind.PART:
            return queryset.filter(part__isnull=False)
        if value == ItemKind.VARIANT:
            return queryset.filter(variant__isnull=False)
        if value == ItemKind.PRODUCT:
            return queryset.filter(product__isnull=False)
        return queryset


class LowStockFilter(SimpleListFilter):
    title = "Минимальный остаток"
    parameter_name = "low"

    def lookups(self, request, model_admin):
        return [("1", "Ниже минимума")]

    def queryset(self, request, queryset):
        if self.value() != "1":
            return queryset
        low_ids = [m.pk for m, _ in reports.low_stock_materials()]
        return queryset.filter(material_id__in=low_ids)


class ReadOnlyAdmin(ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockBalance)
class StockBalanceAdmin(ReadOnlyAdmin):
    list_display = (
        "kind_display",
        "item_display",
        "warehouse",
        "quantity_display",
        "total_display",
        "min_display",
    )
    list_filter = (KindFilter, "warehouse", LowStockFilter)
    search_fields = (
        "material__name",
        "material__color__name",
        "part__name",
        "part_material__name",
        "variant__name",
        "variant__model__name",
        "product__name",
        "product__sku",
    )
    list_filter_submit = False

    def get_queryset(self, request):
        self._totals = reports.totals_by_item()
        return (
            super()
            .get_queryset(request)
            .select_related(
                "warehouse",
                "material__color",
                "part",
                "part_material__color",
                "variant__model",
                "product",
            )
        )

    @display(description="Уровень")
    def kind_display(self, obj):
        return ItemKind(obj.kind).label

    @display(description="Позиция")
    def item_display(self, obj):
        return obj.item_label

    @display(description="На складе", ordering="quantity")
    def quantity_display(self, obj):
        return fmt_qty(obj.quantity, obj.unit_label)

    @display(description="На всех складах")
    def total_display(self, obj):
        return fmt_qty(getattr(self, "_totals", {}).get(obj.item_key), obj.unit_label)

    @display(description="Минимум")
    def min_display(self, obj):
        if not obj.material_id or not obj.material.min_stock:
            return "—"
        total = getattr(self, "_totals", {}).get(obj.item_key) or 0
        text = fmt_qty(obj.material.min_stock, obj.unit_label)
        if total < obj.material.min_stock:
            return format_html("<span style='color:#dc2626;font-weight:600'>{} ↓</span>", text)
        return text


@admin.register(StockMove)
class StockMoveAdmin(ReadOnlyAdmin):
    list_display = (
        "date",
        "document_display",
        "kind_display",
        "item_display",
        "warehouse",
        "quantity_display",
    )
    list_filter = (KindFilter, "warehouse", "date")
    search_fields = (
        "material__name",
        "part__name",
        "variant__name",
        "variant__model__name",
        "product__name",
        "product__sku",
    )
    list_filter_submit = False

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                "warehouse",
                "material__color",
                "part",
                "part_material__color",
                "variant__model",
                "product",
            )
            .prefetch_related("document")
        )

    @display(description="Запись")
    def document_display(self, obj):
        return _doc_link(obj)

    @display(description="Уровень")
    def kind_display(self, obj):
        return ItemKind(obj.kind).label

    @display(description="Позиция")
    def item_display(self, obj):
        return obj.item_label

    @display(description="Количество", ordering="quantity")
    def quantity_display(self, obj):
        sign = "+" if obj.quantity > 0 else ""
        return f"{sign}{fmt_qty(obj.quantity, obj.unit_label)}"


_ = UnfoldAdminSelectWidget  # зарезервировано для зависимых списков (UX-проход)
