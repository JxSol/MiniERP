"""
Экраны склада и журнала производства: документы (закупка, выпуск деталей, сборка,
упаковка, перемещение, инвентаризация), остатки по вкладкам и движения по складу.
"""

from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.http import urlencode
from django.utils.safestring import mark_safe
from unfold.admin import ModelAdmin
from unfold.decorators import action, display
from unfold.views import ChangeList
from unfold.widgets import UnfoldAdminSelectWidget

from catalog.models import Material, MaterialForm, Unit
from core.admin import ArchiveAdmin, HistoryAdmin, LineInline
from core.admin_utils import (
    PageHelpMixin,
    fmt_money,
    fmt_quantity,
    hx_redirect,
)
from core.forms import ActiveChoicesForm, QuantityUnitFormMixin

from . import reports
from .models import (
    Assembly,
    DocumentStatus,
    Inventory,
    InventoryLine,
    ItemKind,
    Packing,
    ProductionRun,
    ReasonDirection,
    Receipt,
    ReceiptLine,
    Receiving,
    ReceivingLine,
    Shortage,
    StockBalance,
    StockMove,
    StockReason,
    Transfer,
    TransferLine,
    Warehouse,
    WriteOff,
    WriteOffLine,
)

ITEM_FIELDS = ("material", "part", "part_material", "variant", "product")
ZERO = Decimal(0)


fmt_qty = fmt_quantity


def _label(text: str, kind: str = "info") -> str:
    """Цветная метка unfold (как в списках) для readonly-полей и колонок."""
    return mark_safe(render_to_string("unfold/helpers/label.html", {"type": kind, "text": text}))


ITEM_ADMIN_URLS = {
    "material": "admin:catalog_material_change",
    "part": "admin:production_part_change",
    "variant": "admin:production_variant_change",
    "product": "admin:sales_product_change",
}


def _item_link(obj) -> str:
    """Название позиции со ссылкой на её карточку (материал, деталь, изделие, товар)."""
    for field, url_name in ITEM_ADMIN_URLS.items():
        pk = getattr(obj, f"{field}_id")
        if pk:
            url = reverse(url_name, args=[pk])
            return format_html('<a href="{}" class="text-primary-600">{}</a>', url, obj.item_label)
    return obj.item_label


def _item_filter_query(obj) -> str:
    """Параметры фильтра журнала движений по этой позиции."""
    params = {}
    for field in ("material", "part", "part_material", "variant", "product"):
        pk = getattr(obj, f"{field}_id")
        if pk:
            params[f"{field}__id__exact"] = pk
    return urlencode(params)


def _doc_link(move: StockMove) -> str:
    doc = move.document
    if doc is None:
        return "—"
    url = reverse(f"admin:{doc._meta.app_label}_{doc._meta.model_name}_change", args=[doc.pk])
    return format_html('<a href="{}" class="text-primary-600">{}</a>', url, doc.number)


# --- Склады ------------------------------------------------------------------------


@admin.register(Warehouse)
class WarehouseAdmin(ArchiveAdmin):
    list_display = ("name", "is_default", "is_showcase", "order", "is_active")
    list_editable = ("order",)
    search_fields = ("name",)
    fields = ("name", "is_default", "is_showcase", "order", "is_active", "notes")


# --- Общее для документов -------------------------------------------------------------


class DocumentForm(ActiveChoicesForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "warehouse" in self.fields and not self.instance.pk:
            default = Warehouse.default()
            if default:
                self.fields["warehouse"].initial = default.pk


STATUS_COLORS = {
    DocumentStatus.DRAFT: "warning",
    DocumentStatus.POSTED: "success",
    DocumentStatus.CANCELLED: "danger",
}


class DocumentAdmin(PageHelpMixin, HistoryAdmin, ModelAdmin):
    """Документ проводится при сохранении; отменить можно кнопкой вверху."""

    form = DocumentForm
    list_filter = ("status", "warehouse")
    readonly_fields = ("number", "status_badge", "status_display", "posted_at", "created_by")
    actions_detail = ("cancel_document",)
    warn_unsaved_form = True
    list_filter_submit = False

    @display(description="Статус", label=STATUS_COLORS)
    def status_badge(self, obj):
        return obj.status, obj.get_status_display()

    @display(description="Статус")
    def status_display(self, obj):
        """Для карточки: в readonly-поле кортеж из status_badge не превращается в метку."""
        return _label(obj.get_status_display(), STATUS_COLORS.get(obj.status, "info"))

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
                "Документ сохранён как черновик и НЕ проведён. " + " ".join(error.messages),
            )
        else:
            messages.success(request, f"{doc.number} проведён, остатки обновлены.")

    def has_cancel_permission(self, request, object_id=None):
        opts = self.model._meta
        return request.user.has_perm(f"{opts.app_label}.change_{opts.model_name}")

    @action(
        description="Отменить документ",
        icon="undo",
        url_path="cancel",
        permissions=["cancel"],
        dialog={
            "title": "Отменить документ",
            "description": "Движения по складу будут удалены, остатки пересчитаны. "
            "Документ останется в истории как отменённый.",
            "form_submit_text": "Отменить документ",
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


# --- Закупки ------------------------------------------------------------------------


class ReceiptLineForm(ActiveChoicesForm):
    class Meta:
        model = ReceiptLine
        fields = (
            "material",
            "supplier",
            "manufacturer",
            "mode",
            "packs",
            "pack_size",
            "quantity_entered",
            "pack_price",
            "total_price",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["total_price"].required = False
        self.fields["pack_price"].label = "Цена за уп. / ед., ₸"


class ReceiptLineInline(LineInline):
    model = ReceiptLine
    form = ReceiptLineForm
    autocomplete_fields = ("material", "supplier", "manufacturer")
    fields = (
        "material",
        "supplier",
        "manufacturer",
        "mode",
        "packs",
        "pack_size",
        "quantity_entered",
        "pack_price",
        "total_price",
        "summary_display",
    )
    readonly_fields = ("summary_display",)
    min_num = 1
    verbose_name_plural = "Позиции"

    class Media:
        # после jquery.init.js, чтобы был доступен django.jQuery
        js = (
            "admin/js/vendor/jquery/jquery.js",
            "admin/js/jquery.init.js",
            "stock/receipt_line.js",
        )

    @display(description="Итого")
    def summary_display(self, obj):
        return obj.summary() if obj.pk else "—"


@admin.register(Receipt)
class ReceiptAdmin(DocumentAdmin):
    list_display = (
        "number",
        "date",
        "warehouse",
        "lines_display",
        "total_display",
        "status_badge",
        "created_by",
    )
    search_fields = ("number", "lines__material__name", "lines__supplier__name")
    inlines = (ReceiptLineInline,)
    fields = ("number", "date", "warehouse", "status_display", "notes", "created_by", "posted_at")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("lines__material__color")

    def get_urls(self):
        info = path(
            "material-info/<int:pk>/",
            self.admin_site.admin_view(self.material_info),
            name="stock_receipt_material_info",
        )
        return [info, *super().get_urls()]

    def material_info(self, request, pk):
        """Данные материала для живого пересчёта строки закупки (JS)."""
        material = get_object_or_404(Material.objects.select_related("color"), pk=pk)
        label, factor = material.direct_unit
        alt = material.alt_factor()
        offers = [
            {
                "supplier": o.supplier_id,
                "manufacturer": o.manufacturer_id,
                "pack_size": f"{o.pack_size.normalize():f}",
                "pack_price": f"{o.pack_price.normalize():f}",
            }
            for o in material.offers.filter(is_active=True)
        ]
        if material.form_type == MaterialForm.ROLL and material.roll_length_m:
            default_pack = f"{material.roll_length_m.normalize():f}"
        else:
            default_pack = offers[0]["pack_size"] if offers else None
        by_packs = material.unit in (Unit.GRAM, Unit.ML) or material.form_type == MaterialForm.ROLL
        return JsonResponse(
            {
                "unit": material.get_unit_display(),
                "direct_unit": label,
                "factor": f"{factor:f}",
                "alt_unit": alt[1] if alt else None,
                "alt_factor": f"{alt[0]:f}" if alt else None,
                "default_pack_size": default_pack,
                "default_mode": "packs" if by_packs else "direct",
                "offers": offers,
            }
        )

    @display(description="Позиции")
    def lines_display(self, obj):
        return "; ".join(
            f"{line.material}: {line.material.format_qty(line.quantity)}"
            for line in obj.lines.all()
        )

    @display(description="Сумма")
    def total_display(self, obj):
        return fmt_money(sum((line.total_price for line in obj.lines.all()), ZERO), 0)


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
        "status_display",
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
        "status_display",
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
        "status_display",
        "notes",
        "created_by",
        "posted_at",
    )


# --- Строки с выбором позиции ------------------------------------------------------


class ItemLineForm(QuantityUnitFormMixin, ActiveChoicesForm):
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


class InventoryLineForm(ItemLineForm):
    quantity_field = "counted_qty"


class TransferLineInline(LineInline):
    model = TransferLine
    form = ItemLineForm
    autocomplete_fields = ITEM_FIELDS
    fields = (*ITEM_FIELDS, "quantity", "entered_unit")
    min_num = 1
    verbose_name_plural = "Позиции"


class InventoryLineInline(LineInline):
    model = InventoryLine
    form = InventoryLineForm
    autocomplete_fields = ITEM_FIELDS
    fields = (*ITEM_FIELDS, "counted_qty", "entered_unit", "difference_display", "reason")
    readonly_fields = ("difference_display",)

    @display(description="Разница")
    def difference_display(self, obj):
        if obj.difference is None:
            return "—"
        sign = "+" if obj.difference > 0 else ("−" if obj.difference < 0 else "")
        return f"{sign}{obj.format_quantity(abs(obj.difference))}"

    min_num = 1
    verbose_name_plural = "Позиции"


@admin.register(Transfer)
class TransferAdmin(DocumentAdmin):
    list_display = ("number", "date", "warehouse", "to_warehouse", "status_badge", "created_by")
    inlines = (TransferLineInline,)
    search_fields = ("number",)
    fields = (
        "number",
        "date",
        ("warehouse", "to_warehouse"),
        "status_display",
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
    fields = ("number", "date", "warehouse", "status_display", "notes", "created_by", "posted_at")

    @display(description="Позиций")
    def lines_count(self, obj):
        return obj.lines.count()


# --- Оприходование и списание ------------------------------------------------------


@admin.register(StockReason)
class StockReasonAdmin(ArchiveAdmin):
    list_display = ("name", "direction", "order", "is_active")
    list_editable = ("order",)
    list_filter = ("direction",)
    search_fields = ("name",)
    fields = ("name", "direction", "order", "is_active", "notes")


class AdjustmentForm(DocumentForm):
    """Причины — только подходящие этому документу: приход, списание или «для обоих»."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reason"].queryset = self.fields["reason"].queryset.filter(
            direction__in=[self._meta.model.direction, ReasonDirection.BOTH]
        )


class ReceivingLineInline(LineInline):
    model = ReceivingLine
    form = ItemLineForm
    autocomplete_fields = ITEM_FIELDS
    fields = (*ITEM_FIELDS, "quantity", "entered_unit")
    min_num = 1
    verbose_name_plural = "Позиции"


class WriteOffLineInline(ReceivingLineInline):
    model = WriteOffLine


class AdjustmentAdmin(DocumentAdmin):
    form = AdjustmentForm
    list_display = (
        "number",
        "date",
        "warehouse",
        "reason",
        "lines_display",
        "status_badge",
        "created_by",
    )
    list_filter = ("status", "warehouse", "reason")
    search_fields = (
        "number",
        "lines__material__name",
        "lines__part__name",
        "lines__variant__name",
        "lines__variant__model__name",
        "lines__product__name",
        "lines__product__sku",
    )
    fields = (
        "number",
        "date",
        "warehouse",
        "reason",
        "status_display",
        "notes",
        "created_by",
        "posted_at",
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("warehouse", "reason", "created_by")
            .prefetch_related(
                "lines__material__color",
                "lines__part",
                "lines__part_material__color",
                "lines__variant__model",
                "lines__product",
            )
        )

    @display(description="Позиции")
    def lines_display(self, obj):
        return "; ".join(
            f"{line.item_label}: {line.quantity_display()}" for line in obj.lines.all()
        )


@admin.register(Receiving)
class ReceivingAdmin(AdjustmentAdmin):
    inlines = (ReceivingLineInline,)


@admin.register(WriteOff)
class WriteOffAdmin(AdjustmentAdmin):
    inlines = (WriteOffLineInline,)


# --- Остатки и движения ------------------------------------------------------------


KIND_FIELDS = {
    ItemKind.MATERIAL.value: "material",
    ItemKind.PART.value: "part",
    ItemKind.VARIANT.value: "variant",
    ItemKind.PRODUCT.value: "product",
}


def filter_kind(queryset, kind):
    field = KIND_FIELDS.get(kind)
    return queryset.filter(**{f"{field}__isnull": False}) if field else queryset


class KindFilter(SimpleListFilter):
    title = "Уровень"
    parameter_name = "kind"

    def lookups(self, request, model_admin):
        return ItemKind.choices

    def queryset(self, request, queryset):
        return filter_kind(queryset, self.value())


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


class ReadOnlyAdmin(PageHelpMixin, ModelAdmin):
    """Остатки и движения считаются системой: только просмотр, без форм редактирования."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# Вкладки остатков: ?kind=material | part | variant | product (без параметра — материалы)
BALANCE_TABS = (
    (ItemKind.MATERIAL.value, "Материалы"),
    (ItemKind.PART.value, "Детали"),
    (ItemKind.VARIANT.value, "Изделия"),
    (ItemKind.PRODUCT.value, "Товары"),
)
DEFAULT_BALANCE_KIND = ItemKind.MATERIAL.value


def balance_kind(request) -> str:
    kind = request.GET.get("kind", DEFAULT_BALANCE_KIND)
    return kind if kind in dict(BALANCE_TABS) else DEFAULT_BALANCE_KIND


class KindTabChangeList(ChangeList):
    """Параметр ?kind= задаёт вкладку, а не фильтр по полю модели."""

    def get_filters_params(self, params=None):
        params = super().get_filters_params(params)
        params.pop("kind", None)
        return params

    def get_queryset(self, request, exclude_parameters=None):
        # Фильтруем корневой queryset, чтобы счётчик «N всего» считал только вкладку
        self.root_queryset = filter_kind(self.root_queryset, balance_kind(request))
        return super().get_queryset(request, exclude_parameters)


@admin.register(StockBalance)
class StockBalanceAdmin(ReadOnlyAdmin):
    """Остатки по вкладкам: материалы, детали, изделия, товары."""

    list_display = ("item_display", "warehouse_display", "quantity_display", "total_display")
    # Карточка остатка: только осмысленные поля (сырые ссылки модели не показываем)
    fields = (
        "item_link",
        "warehouse_display",
        "quantity_detail",
        "available_display",
        "showcase_display",
        "moves_display",
    )
    readonly_fields = fields
    list_filter = ("warehouse",)
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

    def get_changelist(self, request, **kwargs):
        return KindTabChangeList

    def get_list_display(self, request):
        columns = ["item_display", "warehouse_display", "quantity_display"]
        if reports.has_showcase():
            columns += ["available_display", "showcase_display"]
        else:
            columns.append("total_display")
        if balance_kind(request) == ItemKind.MATERIAL.value:
            columns.append("min_display")
        return columns

    def get_list_filter(self, request):
        if balance_kind(request) == ItemKind.MATERIAL.value:
            return ("warehouse", LowStockFilter)
        return ("warehouse",)

    def get_queryset(self, request):
        # «Доступно» — без витрин; образцы на витринах считаются отдельно
        self._totals = reports.totals_by_item(showcase=False)
        self._showcase = reports.totals_by_item(showcase=True)
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

    @display(description="Позиция")
    def item_display(self, obj):
        return obj.item_label

    @display(description="Позиция")
    def item_link(self, obj):
        return _item_link(obj)

    @display(description="Остаток на этом складе")
    def quantity_detail(self, obj):
        return obj.format_quantity(obj.quantity)

    @display(description="Последние движения")
    def moves_display(self, obj):
        moves = (
            StockMove.objects.filter(warehouse_id=obj.warehouse_id)
            .filter(
                **{
                    f: getattr(obj, f)
                    for f in (
                        "material_id",
                        "part_id",
                        "part_material_id",
                        "variant_id",
                        "product_id",
                    )
                }
            )
            .prefetch_related("document")
            .order_by("-date", "-pk")[:20]
        )
        rows = format_html_join(
            "",
            "<tr><td style='padding:4px 12px 4px 0'>{}</td><td style='padding:4px 12px 4px 0'>{}</td>"
            "<td style='padding:4px 0;text-align:right'>{}</td></tr>",
            (
                (
                    move.date.strftime("%d.%m.%Y"),
                    _doc_link(move),
                    ("+" if move.quantity > 0 else "−") + obj.format_quantity(abs(move.quantity)),
                )
                for move in moves
            ),
        )
        all_url = f"{reverse('admin:stock_stockmove_changelist')}?{_item_filter_query(obj)}"
        return format_html(
            "<table>{}</table><p style='margin-top:8px'><a href='{}' class='text-primary-600'>"
            "Все движения по позиции →</a></p>",
            rows,
            all_url,
        )

    @display(description="Склад", ordering="warehouse__name")
    def warehouse_display(self, obj):
        if obj.warehouse.is_showcase:
            return format_html("{} {}", obj.warehouse.name, _label("витрина"))
        return obj.warehouse.name

    @display(description="На складе", ordering="quantity")
    def quantity_display(self, obj):
        return obj.format_quantity(obj.quantity)

    @display(description="На всех складах")
    def total_display(self, obj):
        total = getattr(self, "_totals", {}).get(obj.item_key)
        return obj.format_quantity(total) if total is not None else "—"

    @display(description="Доступно, все склады")
    def available_display(self, obj):
        return obj.format_quantity(getattr(self, "_totals", {}).get(obj.item_key, ZERO))

    @display(description="На витрине")
    def showcase_display(self, obj):
        showcase = getattr(self, "_showcase", {}).get(obj.item_key)
        return obj.format_quantity(showcase) if showcase is not None else "—"

    @display(description="Минимум")
    def min_display(self, obj):
        if not obj.material_id or not obj.material.min_stock:
            return "—"
        total = getattr(self, "_totals", {}).get(obj.item_key) or 0
        text = obj.material.format_qty(obj.material.min_stock)
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
    fields = ("date", "document_display", "item_link", "warehouse", "quantity_display")
    readonly_fields = fields
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

    @display(description="Документ")
    def document_display(self, obj):
        return _doc_link(obj)

    @display(description="Позиция")
    def item_link(self, obj):
        return _item_link(obj)

    @display(description="Уровень")
    def kind_display(self, obj):
        return ItemKind(obj.kind).label

    @display(description="Позиция")
    def item_display(self, obj):
        return obj.item_label

    @display(description="Количество", ordering="quantity")
    def quantity_display(self, obj):
        sign = "+" if obj.quantity > 0 else ""
        return f"{sign}{obj.format_quantity(obj.quantity)}"


_ = UnfoldAdminSelectWidget  # зарезервировано для зависимых списков (UX-проход)
