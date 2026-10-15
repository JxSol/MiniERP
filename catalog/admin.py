from django import forms
from django.contrib import admin
from django.utils.html import format_html, format_html_join, mark_safe
from unfold.decorators import display
from unfold.widgets import UnfoldAdminColorInputWidget

from core.admin import ArchiveAdmin, HistoryAdmin, LineInline
from core.admin_utils import color_swatch, fmt_money, fmt_number

from .models import (
    Color,
    Equipment,
    Material,
    MaterialCategory,
    MaterialColor,
    MaterialPriceHistory,
    OperationType,
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


# --- Палитра цветов ----------------------------------------------------------------


class ColorForm(forms.ModelForm):
    class Meta:
        model = Color
        fields = ("name", "code", "hex", "order", "is_active", "notes")
        widgets = {"hex": UnfoldAdminColorInputWidget}


@admin.register(Color)
class ColorAdmin(ArchiveAdmin):
    form = ColorForm
    list_display = ("swatch", "name", "code", "hex", "order", "is_active")
    list_display_links = ("swatch", "name")
    list_editable = ("order",)
    search_fields = ("name", "code")

    @display(description="")
    def swatch(self, obj):
        return color_swatch(obj.hex, obj.name)


# --- Материалы ---------------------------------------------------------------------


class MaterialColorInline(LineInline):
    model = MaterialColor
    autocomplete_fields = ("color",)
    fields = ("color", "vendor_code", "available")
    verbose_name_plural = "Цвета материала (из палитры)"


class PriceHistoryInline(LineInline):
    model = MaterialPriceHistory
    can_delete = False
    fields = ("changed_at", "pack_size", "pack_price", "unit_price_display", "changed_by")
    readonly_fields = fields
    verbose_name_plural = "История цен"

    def has_add_permission(self, request, obj=None):
        return False

    @display(description="Цена за единицу")
    def unit_price_display(self, obj):
        return fmt_money(obj.unit_price, 4)


@admin.register(Material)
class MaterialAdmin(HistoryAdmin, ArchiveAdmin):
    list_display = (
        "name",
        "category",
        "unit_price_display",
        "pack_display",
        "colors_display",
        "is_active",
    )
    list_filter = ("category__kind", "category")
    search_fields = ("name", "supplier")
    autocomplete_fields = ("category",)
    inlines = (MaterialColorInline, PriceHistoryInline)
    readonly_fields = ("unit_price_display", "where_used")
    fieldsets = (
        (
            "Основное",
            {
                "classes": ["tab"],
                "fields": (
                    "name",
                    "category",
                    "unit",
                    ("pack_size", "pack_price"),
                    "unit_price_display",
                    "supplier",
                    "url",
                    "is_active",
                    "notes",
                ),
            },
        ),
        ("Где используется", {"classes": ["tab"], "fields": ("where_used",)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("category").prefetch_related("colors")

    def save_model(self, request, obj, form, change):
        obj._changed_by = request.user
        super().save_model(request, obj, form, change)

    @display(description="Цена за единицу", ordering="pack_price")
    def unit_price_display(self, obj):
        if not obj.pk and obj.pack_price is None:
            return "—"
        return f"{fmt_money(obj.unit_price, 4)} / {obj.get_unit_display()}"

    @display(description="Закупка")
    def pack_display(self, obj):
        return (
            f"{fmt_number(obj.pack_size)} {obj.get_unit_display()} за {fmt_money(obj.pack_price)}"
        )

    @display(description="Цвета")
    def colors_display(self, obj):
        return format_html_join(
            "", "{}", ((color_swatch(c.hex, c.name),) for c in obj.colors.all())
        )

    @display(description="Где используется")
    def where_used(self, obj):
        if not obj.pk:
            return "—"
        groups = [
            ("Детали (материал по умолчанию)", [str(p) for p in obj.parts.all()]),
            (
                "Варианты (цвет в слоте)",
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
