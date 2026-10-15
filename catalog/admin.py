from django import forms
from django.contrib import admin
from django.db.models import Avg, DecimalField, ExpressionWrapper, F, Q
from django.utils.html import format_html, format_html_join, mark_safe
from unfold.decorators import display
from unfold.widgets import UnfoldAdminColorInputWidget

from core.admin import ArchiveAdmin, HistoryAdmin, LineInline
from core.admin_utils import color_swatch, fmt_money, fmt_number, fmt_price

from .models import (
    Color,
    Equipment,
    Manufacturer,
    Material,
    MaterialCategory,
    MaterialOffer,
    MaterialPriceHistory,
    OperationType,
    Supplier,
)

# --- Категории материалов ----------------------------------------------------------


class MaterialCategoryForm(forms.ModelForm):
    class Meta:
        model = MaterialCategory
        fields = ("name", "parent", "kind", "is_active", "notes")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].required = False

    def clean(self):
        data = super().clean()
        parent = data.get("parent")
        if parent:
            data["kind"] = parent.kind
            self.instance.kind = parent.kind
        elif not data.get("kind"):
            self.add_error("kind", "У категории верхнего уровня нужно выбрать вид материала.")
        return data


@admin.register(MaterialCategory)
class MaterialCategoryAdmin(ArchiveAdmin):
    form = MaterialCategoryForm
    list_display = ("path", "kind", "is_active")
    list_filter = ("kind",)
    search_fields = ("name", "path")
    ordering = ("path",)


# --- Палитра, производители, поставщики ---------------------------------------------


class ColorForm(forms.ModelForm):
    class Meta:
        model = Color
        fields = ("name", "hex", "order", "is_active", "notes")
        widgets = {"hex": UnfoldAdminColorInputWidget}


@admin.register(Color)
class ColorAdmin(ArchiveAdmin):
    form = ColorForm
    list_display = ("swatch", "name", "hex", "order", "is_active")
    list_display_links = ("swatch", "name")
    list_editable = ("order",)
    search_fields = ("name",)

    @display(description="")
    def swatch(self, obj):
        return color_swatch(obj.hex, obj.name)


@admin.register(Manufacturer)
class ManufacturerAdmin(ArchiveAdmin):
    list_display = ("name", "url", "is_active")
    search_fields = ("name",)
    fields = ("name", "url", "is_active", "notes")


@admin.register(Supplier)
class SupplierAdmin(ArchiveAdmin):
    list_display = ("name", "url", "is_active")
    search_fields = ("name",)
    fields = ("name", "url", "is_active", "notes")


# --- Материалы ---------------------------------------------------------------------


class MaterialOfferInline(LineInline):
    model = MaterialOffer
    autocomplete_fields = ("manufacturer", "supplier")
    fields = (
        "is_active",
        "manufacturer",
        "supplier",
        "pack_size",
        "pack_price",
        "unit_price_display",
        "url",
    )
    readonly_fields = ("unit_price_display",)
    verbose_name_plural = "Предложения поставщиков"

    @display(description="За единицу")
    def unit_price_display(self, obj):
        if not obj.pk:
            return "—"
        return fmt_price(obj.unit_price)


class PriceHistoryInline(LineInline):
    model = MaterialPriceHistory
    can_delete = False
    fields = ("changed_at", "offer", "pack_size", "pack_price", "unit_price_display", "changed_by")
    readonly_fields = fields
    verbose_name_plural = "История цен"

    def has_add_permission(self, request, obj=None):
        return False

    @display(description="За единицу")
    def unit_price_display(self, obj):
        return fmt_price(obj.unit_price)


@admin.register(Material)
class MaterialAdmin(HistoryAdmin, ArchiveAdmin):
    list_display = (
        "swatch",
        "full_name_display",
        "category",
        "unit_price_display",
        "offers_display",
        "is_active",
    )
    list_display_links = ("swatch", "full_name_display")
    list_filter = ("category__kind", "category", "color")
    search_fields = ("name", "color__name", "offers__manufacturer__name", "offers__supplier__name")
    autocomplete_fields = ("category", "color")
    inlines = (MaterialOfferInline, PriceHistoryInline)
    readonly_fields = ("unit_price_display", "where_used")
    fieldsets = (
        (
            "Данные",
            {
                "classes": ["tab"],
                "fields": (
                    "name",
                    "color",
                    "category",
                    "unit",
                    "unit_price_display",
                    "is_active",
                    "notes",
                ),
            },
        ),
        ("Где используется", {"classes": ["tab"], "fields": ("where_used",)}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("category", "color")
            .prefetch_related("offers__manufacturer", "offers__supplier")
            .annotate(
                unit_price_db=Avg(
                    ExpressionWrapper(
                        F("offers__pack_price") / F("offers__pack_size"),
                        output_field=DecimalField(),
                    ),
                    filter=Q(offers__is_active=True),
                )
            )
        )

    def save_formset(self, request, form, formset, change):
        if formset.model is MaterialOffer:
            for offer in formset.save(commit=False):
                offer._changed_by = request.user
                offer.save()
            for offer in formset.deleted_objects:
                offer.delete()
            formset.save_m2m()
            return
        super().save_formset(request, form, formset, change)

    @display(description="")
    def swatch(self, obj):
        return color_swatch(obj.color.hex, obj.color.name) if obj.color_id else ""

    @display(description="Материал", ordering="name")
    def full_name_display(self, obj):
        return obj.full_name

    @display(description="Цена за единицу (средняя)", ordering="unit_price_db")
    def unit_price_display(self, obj):
        if not obj.pk:
            return "Посчитается из предложений поставщиков после сохранения"
        price = obj.unit_price
        if price is None:
            return format_html(
                "<span style='color:#b45309'>нет актуальных предложений — "
                "себестоимость не считается</span>"
            )
        return f"{fmt_price(price)} / {obj.get_unit_display()}"

    @display(description="Предложения")
    def offers_display(self, obj):
        offers = obj.active_offers
        if not offers:
            return format_html("<span style='color:#b45309'>нет</span>")
        return format_html_join(
            mark_safe("<br>"),
            "{}: {}",
            (
                (
                    " · ".join(str(x) for x in (o.manufacturer, o.supplier) if x) or "—",
                    f"{fmt_number(o.pack_size)} {obj.get_unit_display()} "
                    f"за {fmt_money(o.pack_price, 0)}",
                )
                for o in offers
            ),
        )

    @display(description="Где используется")
    def where_used(self, obj):
        if not obj.pk:
            return "—"
        groups = [
            ("Детали (материал по умолчанию)", [str(p) for p in obj.parts.all()]),
            (
                "Варианты (в слоте)",
                sorted(
                    {str(s.variant) for s in obj.variant_slots.select_related("variant__model")}
                ),
            ),
            (
                "Покупное в моделях",
                sorted({str(c.model) for c in obj.model_components.select_related("model")}),
            ),
            (
                "Упаковка товаров",
                sorted({str(p.product) for p in obj.product_packaging.select_related("product")}),
            ),
        ]
        html = [
            format_html(
                "<p style='margin-bottom:4px'><b>{}</b> ({}):</p><p style='margin-bottom:12px'>{}</p>",
                title,
                len(items),
                ", ".join(items) or "—",
            )
            for title, items in groups
        ]
        return mark_safe("".join(html))  # noqa: S308 — части уже экранированы format_html


# --- Оборудование ------------------------------------------------------------------


@admin.register(Equipment)
class EquipmentAdmin(HistoryAdmin, ArchiveAdmin):
    list_display = (
        "name",
        "technology",
        "quantity",
        "hourly_display",
        "scrap_display",
        "is_active",
    )
    list_filter = ("technology",)
    search_fields = ("name",)
    readonly_fields = (
        "amortization_display",
        "electricity_display",
        "hourly_display",
        "scrap_display",
    )
    fieldsets = (
        ("Основное", {"fields": ("name", "technology", "quantity", "is_active", "notes")}),
        (
            "Стоимость часа работы",
            {
                "fields": (
                    ("purchase_price", "maintenance_cost", "residual_value"),
                    ("resource_hours", "power_kw"),
                    "amortization_display",
                    "electricity_display",
                    "hourly_display",
                )
            },
        ),
        ("Брак", {"fields": ("scrap_percent", "scrap_display")}),
    )

    def _ready(self, obj):
        return obj.pk and obj.resource_hours

    @display(description="Амортизация, ₸/ч")
    def amortization_display(self, obj):
        return fmt_money(obj.amortization_per_hour) if self._ready(obj) else "—"

    @display(description="Электричество, ₸/ч")
    def electricity_display(self, obj):
        return fmt_money(obj.electricity_per_hour) if self._ready(obj) else "—"

    @display(description="Стоимость часа, ₸/ч")
    def hourly_display(self, obj):
        if not self._ready(obj):
            return "—"
        return fmt_money(obj.amortization_per_hour + obj.electricity_per_hour)

    @display(description="% брака (итоговый)")
    def scrap_display(self, obj):
        source = "свой" if obj.scrap_percent is not None else "из общих настроек"
        return f"{fmt_number(obj.effective_scrap_percent)} % ({source})"


# --- Типы операций -----------------------------------------------------------------


@admin.register(OperationType)
class OperationTypeAdmin(HistoryAdmin, ArchiveAdmin):
    list_display = ("name", "level", "rate_display", "default_minutes", "order", "is_active")
    list_filter = ("level",)
    list_editable = ("order",)
    search_fields = ("name",)
    fields = ("name", "level", "rate", "default_minutes", "order", "is_active", "notes")

    @display(description="Ставка, ₸/ч")
    def rate_display(self, obj):
        if obj.rate is None:
            return f"{fmt_money(obj.effective_rate())} (из настроек)"
        return fmt_money(obj.rate)
