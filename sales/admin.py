from django.contrib import admin, messages
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.html import format_html
from unfold.decorators import action, display
from unfold.widgets import UnfoldAdminCheckboxSelectMultipleWidget

from core.admin import ArchiveAdmin, HistoryAdmin, LineInline
from core.admin_utils import (
    COST_PERMISSION,
    CostVisibilityMixin,
    fmt_money,
    fmt_number,
    hx_redirect,
    photo_square,
    render_cost_tree,
    safe_tree,
    thumbnail,
)
from core.models import SiteSettings
from costing.engine import direct_cost
from costing.services import fix_cost, product_tree
from gallery.admin import PhotoUploaderMixin, ProductPhotoInline
from gallery.models import main_photo_for_product

from .models import (
    CostSnapshot,
    Product,
    ProductItem,
    ProductOperation,
    ProductPackaging,
    ProductStatus,
)


class ProductItemInline(LineInline):
    model = ProductItem
    autocomplete_fields = ("variant",)
    fields = ("variant", "quantity")
    verbose_name_plural = "Состав"


class ProductPackagingInline(LineInline):
    model = ProductPackaging
    autocomplete_fields = ("material",)
    fields = ("material", "quantity")
    verbose_name_plural = "Упаковка"


class ProductOperationInline(LineInline):
    model = ProductOperation
    fields = ("operation_type", "minutes", "rate")
    verbose_name_plural = "Комплектация"


class CostSnapshotInline(LineInline):
    model = CostSnapshot
    can_delete = False
    shows_cost = True
    fields = ("created_at", "created_by", "direct_display", "total_display")
    readonly_fields = fields
    verbose_name_plural = "Снимки себестоимости"

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @display(description="Прямая")
    def direct_display(self, obj):
        return fmt_money(obj.direct)

    @display(description="Полная")
    def total_display(self, obj):
        return fmt_money(obj.total)


def _growth(product: Product):
    """Рост себестоимости с момента фиксации, %."""
    snapshot = product.last_snapshot
    if not snapshot or product.cost_cached is None or not snapshot.total:
        return None
    return (product.cost_cached - snapshot.total) / snapshot.total * 100


@admin.register(Product)
class ProductAdmin(PhotoUploaderMixin, CostVisibilityMixin, HistoryAdmin, ArchiveAdmin):
    list_display = (
        "photo",
        "sku",
        "name",
        "kind",
        "status_badge",
        "cost_display",
        "fixed_display",
        "growth_display",
    )
    list_display_links = ("photo", "sku", "name")
    list_filter = ("status", "kind", "tags")
    search_fields = ("sku", "name", "common_barcode", "items__variant__model__name")
    inlines = (
        ProductItemInline,
        ProductPackagingInline,
        ProductOperationInline,
        ProductPhotoInline,
        CostSnapshotInline,
    )
    readonly_fields = (
        "sku",
        "main_photo_display",
        "problems_display",
        "cost_tree",
        "fixed_display",
        "growth_display",
    )
    cost_fields = ("cost_display", "fixed_display", "growth_display", "cost_tree")
    actions = ("fix_cost_bulk",)
    actions_detail = ("fix_cost_detail",)
    fieldsets = (
        (
            "Данные",
            {
                "classes": ["tab"],
                "fields": (
                    "main_photo_display",
                    "sku",
                    "name",
                    "kind",
                    ("status", "problems_display"),
                    "tags",
                    "common_barcode",
                    ("length_cm", "width_cm", "height_cm", "weight_g"),
                    "description",
                    "is_active",
                    "notes",
                ),
            },
        ),
        (
            "Себестоимость",
            {"classes": ["tab"], "fields": ("cost_tree", "fixed_display", "growth_display")},
        ),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("snapshots")

    @display(description="Главное фото")
    def main_photo_display(self, obj):
        return photo_square(main_photo_for_product(obj) if obj.pk else None)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "tags":
            kwargs["widget"] = UnfoldAdminCheckboxSelectMultipleWidget
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    # --- Колонки и поля ---

    @display(description="")
    def photo(self, obj):
        p = main_photo_for_product(obj)
        return thumbnail(p.preview_url if p else "", 40)

    @display(
        description="Статус",
        label={
            ProductStatus.DRAFT: "warning",
            ProductStatus.ACTIVE: "success",
            ProductStatus.RETIRED: "danger",
        },
    )
    def status_badge(self, obj):
        return obj.status, obj.get_status_display()

    @display(description="Себестоимость", ordering="cost_cached")
    def cost_display(self, obj):
        return fmt_money(obj.cost_cached)

    @display(description="Зафиксирована")
    def fixed_display(self, obj):
        snapshot = obj.last_snapshot if obj.pk else None
        if not snapshot:
            return "Не фиксировалась"
        who = f", {snapshot.created_by}" if snapshot.created_by else ""
        return format_html(
            "{} <span style='opacity:.6'>({:%d.%m.%Y}{})</span>",
            fmt_money(snapshot.total),
            snapshot.created_at,
            who,
        )

    @display(description="Рост с фиксации")
    def growth_display(self, obj):
        growth = _growth(obj) if obj.pk else None
        if growth is None:
            return "—"
        alert = growth >= SiteSettings.load().cost_growth_alert_percent
        sign = "+" if growth > 0 else ""
        diff = obj.cost_cached - obj.last_snapshot.total
        return format_html(
            "<span style='{}'>{}{} ({}{} %)</span>",
            "color:#dc2626;font-weight:600" if alert else "",
            sign,
            fmt_money(diff),
            sign,
            fmt_number(growth.quantize(growth.__class__("0.1"))),
        )

    @display(description="Чего не хватает для активации")
    def problems_display(self, obj):
        if not obj.pk:
            return "—"
        problems = obj.activation_problems()
        if not problems:
            return format_html("<span style='color:#16a34a'>Всё заполнено ✓</span>")
        return format_html(
            "<ul style='color:#b45309;margin:0;padding-left:16px'>{}</ul>",
            format_html("".join(["<li>{}</li>"] * len(problems)), *problems),
        )

    @display(description="Разбивка")
    def cost_tree(self, obj):
        if not obj.pk:
            return "Посчитается после сохранения"
        tree = safe_tree(product_tree, obj)
        if isinstance(tree, Exception):
            return render_cost_tree(tree)
        return render_cost_tree(
            tree,
            footer=[
                ("Прямая себестоимость", direct_cost(tree)),
                ("Полная себестоимость", tree.amount),
            ],
        )

    # --- Проверка перед активацией (ТЗ п. 5.6, правило 8) ---

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        product = form.instance
        if product.status == ProductStatus.ACTIVE:
            problems = product.activation_problems()
            if problems:
                product.status = ProductStatus.DRAFT
                product._history_user = request.user
                product.save(update_fields=["status"])
                messages.error(
                    request,
                    "Товар сохранён как черновик — для активации не хватает: "
                    + "; ".join(problems)
                    + ".",
                )

    # --- Фиксация себестоимости ---

    def has_fix_cost_permission(self, request, object_id=None):
        return request.user.has_perm(COST_PERMISSION) and request.user.has_perm(
            "sales.add_costsnapshot"
        )

    @action(description="Зафиксировать себестоимость", permissions=["fix_cost"])
    def fix_cost_bulk(self, request, queryset):
        for product in queryset:
            fix_cost(product, request.user)
        messages.success(request, f"Зафиксирована себестоимость товаров: {queryset.count()}.")

    @action(
        description="Зафиксировать себестоимость",
        icon="lock",
        url_path="fix-cost",
        permissions=["fix_cost"],
        dialog={
            "title": "Зафиксировать себестоимость",
            "description": "Текущая себестоимость сохранится как снимок. Снимки не редактируются.",
            "form_submit_text": "Зафиксировать",
        },
    )
    def fix_cost_detail(self, request, form, object_id):
        product = get_object_or_404(Product, pk=object_id)
        snapshot = fix_cost(product, request.user)
        messages.success(request, f"Себестоимость зафиксирована: {fmt_money(snapshot.total)}.")
        return hx_redirect(request, reverse("admin:sales_product_change", args=[object_id]))
