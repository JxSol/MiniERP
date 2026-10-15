from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.forms.models import BaseInlineFormSet
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from unfold.decorators import action, display
from unfold.forms import BaseDialogForm
from unfold.widgets import (
    UnfoldAdminCheckboxSelectMultipleWidget,
    UnfoldAdminDecimalFieldWidget,
    UnfoldAdminIntegerFieldWidget,
    UnfoldAdminSelectWidget,
    UnfoldBooleanSwitchWidget,
)

from catalog.models import Color, Material, MaterialKind
from core.admin import ArchiveAdmin, HistoryAdmin, LineInline
from core.admin_utils import (
    CostVisibilityMixin,
    can_view_cost,
    color_swatch,
    fmt_money,
    fmt_number,
    hx_redirect,
    render_cost_tree,
    thumbnail,
)
from core.forms import ActiveChoicesForm
from core.models import Size
from costing.services import model_tree, part_tree, variant_tree
from gallery.admin import ModelPhotoInline, PartPhotoInline, PhotoUploaderMixin, VariantPhotoInline
from gallery.models import main_photo

from . import generators, services
from .models import (
    Coloring,
    ModelComponent,
    ModelOperation,
    ModelPart,
    Part,
    PartOperation,
    ProductCategory,
    ProductModel,
    Variant,
    VariantSlot,
    clean_slot,
)


def _admin_url(obj, action="change"):
    return reverse(f"admin:{obj._meta.app_label}_{obj._meta.model_name}_{action}", args=[obj.pk])


def _link(obj, text=None):
    return format_html('<a href="{}" class="text-primary-600">{}</a>', _admin_url(obj), text or obj)


# --- Категории изделий -------------------------------------------------------------


@admin.register(ProductCategory)
class ProductCategoryAdmin(ArchiveAdmin):
    list_display = ("path", "is_active")
    search_fields = ("name", "path")
    fields = ("name", "parent", "is_active", "notes")
    ordering = ("path",)


# --- Детали ------------------------------------------------------------------------


class PartOperationInline(LineInline):
    model = PartOperation
    fields = ("operation_type", "minutes", "rate")
    verbose_name_plural = "Обработка"


@admin.register(Part)
class PartAdmin(PhotoUploaderMixin, CostVisibilityMixin, HistoryAdmin, ArchiveAdmin):
    list_display = (
        "name",
        "code",
        "equipment",
        "material",
        "per_piece_display",
        "cost_display",
        "is_active",
    )
    list_filter = ("equipment", "material__category")
    search_fields = ("name", "code")
    autocomplete_fields = ("equipment", "material")
    inlines = (PartOperationInline, PartPhotoInline)
    readonly_fields = ("per_piece_display", "scrap_display", "cost_tree", "used_in")
    cost_fields = ("cost_display", "cost_tree")
    actions_detail = ("copy_part",)
    fieldsets = (
        (
            "Основное",
            {
                "classes": ["tab"],
                "fields": (
                    "name",
                    "code",
                    "equipment",
                    "material",
                    ("batch_material_qty", "batch_minutes", "batch_size"),
                    "per_piece_display",
                    ("scrap_percent", "scrap_display"),
                    "is_active",
                ),
            },
        ),
        (
            "Файлы и параметры",
            {"classes": ["tab"], "fields": ("model_file", "model_url", "print_params", "notes")},
        ),
        ("Себестоимость", {"classes": ["tab"], "fields": ("cost_tree",)}),
        ("Где используется", {"classes": ["tab"], "fields": ("used_in",)}),
    )

    @display(description="На 1 шт")
    def per_piece_display(self, obj):
        if not obj.pk:
            return "Посчитается после сохранения"
        minutes = obj.batch_minutes / obj.batch_size
        return (
            f"{fmt_number(obj.material_per_piece.quantize(obj.batch_material_qty))} "
            f"{obj.material.get_unit_display()}, {fmt_number(minutes.quantize(obj.batch_minutes))} мин"
        )

    @display(description="% брака (итоговый)")
    def scrap_display(self, obj):
        if not obj.pk:
            return "—"
        if obj.scrap_percent is not None:
            return f"{fmt_number(obj.scrap_percent)} % (свой)"
        return f"{fmt_number(obj.equipment.effective_scrap_percent)} % (из станка или настроек)"

    @display(description="Себестоимость 1 шт", ordering="cost_cached")
    def cost_display(self, obj):
        return fmt_money(obj.cost_cached)

    @display(description="Разбивка")
    def cost_tree(self, obj):
        return render_cost_tree(part_tree(obj)) if obj.pk else "Посчитается после сохранения"

    @display(description="Входит в модели")
    def used_in(self, obj):
        if not obj.pk:
            return "—"
        lines = obj.model_lines.select_related("model")
        return (
            format_html_join(
                "", "<p>{} × {}</p>", ((_link(line.model), line.quantity) for line in lines)
            )
            or "Пока ни в одной модели"
        )

    def has_copy_permission(self, request, object_id=None):
        return request.user.has_perm("production.add_part")

    @action(
        description="Копировать деталь",
        icon="content_copy",
        url_path="copy-part",
        permissions=["copy"],
        dialog={
            "title": "Копировать деталь",
            "description": "Создастся копия со всеми параметрами и операциями обработки.",
            "form_submit_text": "Копировать",
        },
    )
    def copy_part(self, request, form, object_id):
        part = get_object_or_404(Part, pk=object_id)
        copy = generators.copy_part(part)
        messages.success(request, f"Создана копия «{copy.name}». Поменяйте название и параметры.")
        return hx_redirect(request, _admin_url(copy))


# --- Модели ------------------------------------------------------------------------


class ModelPartFormSet(BaseInlineFormSet):
    """Детали одного слота должны быть из одной категории материалов."""

    def clean(self):
        super().clean()
        categories: dict[str, tuple] = {}
        for form in self.forms:
            if not hasattr(form, "cleaned_data") or form.cleaned_data.get("DELETE"):
                continue
            part = form.cleaned_data.get("part")
            slot = clean_slot(form.cleaned_data.get("slot") or "")
            if not part or not slot:
                continue
            category = part.material.category
            if slot in categories and categories[slot][0] != category.pk:
                raise ValidationError(
                    f"В слоте «{slot}» детали из разных категорий: «{categories[slot][1]}» и "
                    f"«{category}». Разнесите их по разным слотам."
                )
            categories[slot] = (category.pk, category)


class ModelPartInline(LineInline):
    model = ModelPart
    formset = ModelPartFormSet
    autocomplete_fields = ("part",)
    fields = ("part", "quantity", "slot")
    verbose_name_plural = "Детали"


class ModelComponentInline(LineInline):
    model = ModelComponent
    autocomplete_fields = ("material",)
    fields = ("material", "quantity")
    verbose_name_plural = "Покупное"


class ModelOperationInline(LineInline):
    model = ModelOperation
    fields = ("operation_type", "minutes", "rate")
    verbose_name_plural = "Сборка"


class ColorVariantsForm(BaseDialogForm):
    material = forms.ModelChoiceField(
        label="Материал",
        queryset=Material.objects.none(),
        widget=UnfoldAdminSelectWidget,
        help_text="Только материалы той же категории, что у деталей со слотами.",
    )
    colors = forms.ModelMultipleChoiceField(
        label="Цвета",
        queryset=Color.objects.filter(is_active=True),
        widget=UnfoldAdminCheckboxSelectMultipleWidget,
        required=False,
        help_text="Отметьте цвета — для каждого создастся вариант. Нужны цвета, которые есть у материала.",
    )
    add_random = forms.BooleanField(
        label="Добавить вариант «Ассорти» (случайный цвет)",
        required=False,
        widget=UnfoldBooleanSwitchWidget,
    )
    create_products = forms.BooleanField(
        label="Сразу создать поштучные товары",
        required=False,
        initial=True,
        widget=UnfoldBooleanSwitchWidget,
    )

    def __init__(self, request, object_id=None, *args, **kwargs):
        super().__init__(request, object_id, *args, **kwargs)
        self.model_obj = ProductModel.objects.get(pk=object_id)
        categories = generators.slot_category_ids(self.model_obj)
        materials = Material.objects.filter(is_active=True, category_id__in=categories)
        self.fields["material"].queryset = materials
        self.fields["colors"].queryset = Color.objects.filter(
            is_active=True, materials__in=materials
        ).distinct()

    def clean(self):
        data = super().clean()
        material, colors = data.get("material"), data.get("colors") or []
        if material:
            available = set(material.material_colors.values_list("color_id", flat=True))
            missing = [c.name for c in colors if c.pk not in available]
            if missing:
                self.add_error(
                    "colors",
                    f"У материала «{material}» нет цветов: {', '.join(missing)}. "
                    "Добавьте их в карточке материала.",
                )
        if not colors and not data.get("add_random"):
            self.add_error("colors", "Отметьте хотя бы один цвет или «Ассорти».")
        return data


class SetForm(BaseDialogForm):
    variants = forms.ModelMultipleChoiceField(
        label="Варианты",
        queryset=Variant.objects.none(),
        widget=UnfoldAdminCheckboxSelectMultipleWidget,
        help_text="Один вариант — набор одного цвета. Несколько — ровный микс, поровну каждого.",
    )
    total = forms.IntegerField(
        label="Штук в наборе", min_value=2, initial=10, widget=UnfoldAdminIntegerFieldWidget
    )
    packaging = forms.ModelChoiceField(
        label="Упаковка набора",
        queryset=Material.objects.filter(is_active=True, category__kind=MaterialKind.PACKAGING),
        required=False,
        widget=UnfoldAdminSelectWidget,
    )
    packing_minutes = forms.DecimalField(
        label="Комплектация, мин",
        required=False,
        min_value=Decimal("0.01"),
        widget=UnfoldAdminDecimalFieldWidget,
        help_text="Пусто — время по умолчанию из типа операции «Комплектация набора».",
    )

    def __init__(self, request, object_id=None, *args, **kwargs):
        super().__init__(request, object_id, *args, **kwargs)
        self.model_obj = ProductModel.objects.get(pk=object_id)
        self.fields["variants"].queryset = self.model_obj.variants.filter(is_active=True)


class SizeForm(BaseDialogForm):
    size = forms.ModelChoiceField(
        label="Размер", queryset=Size.objects.none(), widget=UnfoldAdminSelectWidget
    )

    def __init__(self, request, object_id=None, *args, **kwargs):
        super().__init__(request, object_id, *args, **kwargs)
        model = ProductModel.objects.get(pk=object_id)
        base = model.base_model or model
        self.fields["size"].queryset = Size.objects.filter(is_active=True).exclude(
            pk__in=base.sizes.values("size")
        )


@admin.register(ProductModel)
class ProductModelAdmin(PhotoUploaderMixin, CostVisibilityMixin, HistoryAdmin, ArchiveAdmin):
    list_display = (
        "photo",
        "name",
        "code",
        "category",
        "size",
        "status",
        "variants_display",
        "cost_display",
    )
    list_display_links = ("photo", "name")
    list_filter = ("status", "category", "tags", "size")
    search_fields = ("name", "code")
    autocomplete_fields = ("category", "default_packaging", "base_model")
    filter_horizontal = ()
    inlines = (ModelPartInline, ModelComponentInline, ModelOperationInline, ModelPhotoInline)
    readonly_fields = ("variants_overview", "products_overview", "sizes_overview", "cost_tree")
    cost_fields = ("cost_display", "cost_tree")
    actions_detail = ("create_variants", "create_set", "create_size")
    fieldsets = (
        (
            "Основное",
            {
                "classes": ["tab"],
                "fields": (
                    "name",
                    "code",
                    "category",
                    "tags",
                    "status",
                    "description",
                    ("default_packaging", "default_packing_minutes"),
                    "marketplace_group",
                    "is_active",
                    "notes",
                ),
            },
        ),
        ("Варианты", {"classes": ["tab"], "fields": ("variants_overview",)}),
        ("Товары", {"classes": ["tab"], "fields": ("products_overview",)}),
        ("Размеры", {"classes": ["tab"], "fields": ("base_model", "size", "sizes_overview")}),
        ("Себестоимость", {"classes": ["tab"], "fields": ("cost_tree",)}),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("category", "size")
            .prefetch_related("variants__slots__color")
            .annotate(variant_count=Count("variants"))
        )

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "tags":
            kwargs["widget"] = UnfoldAdminCheckboxSelectMultipleWidget
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    @display(description="")
    def photo(self, obj):
        p = main_photo(model=obj)
        return thumbnail(p.preview_url if p else "", 40)

    @display(description="Варианты")
    def variants_display(self, obj):
        swatches = []
        for variant in obj.variants.all():
            colors = [s.color for s in variant.slots.all() if s.color]
            if variant.coloring == Coloring.RANDOM:
                swatches.append(format_html('<span title="{}">🎲</span>', variant.name))
            for color in colors[:1]:
                swatches.append(color_swatch(color.hex, variant.name))
        return format_html("".join(["{}"] * len(swatches)), *swatches) if swatches else "—"

    @display(description="Себестоимость", ordering="cost_cached")
    def cost_display(self, obj):
        return fmt_money(obj.cost_cached)

    @display(description="Варианты модели")
    def variants_overview(self, obj):
        if not obj.pk:
            return "Сохраните модель, затем создайте варианты кнопкой «Создать варианты» вверху."
        rows = []
        show_cost = can_view_cost()
        for v in obj.variants.prefetch_related("slots__color", "slots__material"):
            slots = format_html_join(
                ", ",
                "{}{}",
                (
                    (
                        color_swatch(s.color.hex, s.color.name) if s.color else "",
                        f"{s.slot}: {s.color or s.material}",
                    )
                    for s in v.slots.all()
                ),
            )
            rows.append(
                format_html(
                    "<tr><td style='padding:4px 8px'>{}</td><td style='padding:4px 8px'>{}</td>"
                    "<td style='padding:4px 8px'>{}</td><td style='padding:4px 8px'>{}</td>"
                    "<td style='padding:4px 8px;text-align:right'>{}</td></tr>",
                    _link(v, v.name),
                    v.code,
                    v.get_coloring_display(),
                    slots or "—",
                    fmt_money(v.cost_cached) if show_cost else "",
                )
            )
        add_url = reverse("admin:production_variant_add") + f"?model={obj.pk}"
        table = (
            format_html(
                "<table style='width:100%;font-size:13px'>{}</table>",
                format_html("".join(["{}"] * len(rows)), *rows),
            )
            if rows
            else "Вариантов пока нет."
        )
        return format_html(
            "{}<p style='margin-top:8px'><a href='{}' class='text-primary-600'>+ Вариант вручную</a> "
            "(многоцветный, например «Пастельный»)</p>",
            table,
            add_url,
        )

    @display(description="Товары с этой моделью")
    def products_overview(self, obj):
        if not obj.pk:
            return "—"
        from sales.models import Product

        products = Product.objects.filter(items__variant__model=obj).distinct()
        return (
            format_html_join("", "<p>{} — {}</p>", ((p.sku, _link(p, p.name)) for p in products))
            or "Товаров пока нет. Их создают кнопки «Создать варианты» и «Создать набор»."
        )

    @display(description="Размеры")
    def sizes_overview(self, obj):
        if not obj.pk:
            return "—"
        base = obj.base_model or obj
        items = [base, *base.sizes.select_related("size")]
        return format_html_join(
            "",
            "<p>{}{}</p>",
            ((_link(m), " ← эта модель" if m.pk == obj.pk else "") for m in items),
        )

    @display(description="Разбивка (материалы деталей по умолчанию)")
    def cost_tree(self, obj):
        return render_cost_tree(model_tree(obj)) if obj.pk else "Посчитается после сохранения"

    def save_related(self, request, form, formsets, change):
        """Переименование слота не должно стирать цвета вариантов (см. services)."""
        model = form.instance
        before = services.snapshot_slots(model) if change else {}
        renames: dict[str, str] = {}
        for formset in formsets:
            if formset.model is not ModelPart:
                continue
            for line_form in formset.forms:
                if not getattr(line_form, "cleaned_data", None) or line_form.cleaned_data.get(
                    "DELETE"
                ):
                    continue
                old = clean_slot(line_form.initial.get("slot") or "")
                new = clean_slot(line_form.cleaned_data.get("slot") or "")
                if old and new and old != new:
                    renames[old] = new
        super().save_related(request, form, formsets, change)
        if renames:
            still_present = set(model.slot_names())
            renames = {o: n for o, n in renames.items() if o not in still_present}
            services.restore_renamed_slots(model, before, renames)

    # --- Генераторы ---

    def has_generate_permission(self, request, object_id=None):
        return request.user.has_perm("production.add_variant") and request.user.has_perm(
            "sales.add_product"
        )

    def has_generate_size_permission(self, request, object_id=None):
        return request.user.has_perm(
            "production.add_productmodel"
        ) and self.has_generate_permission(request)

    @action(
        description="Создать варианты",
        icon="palette",
        url_path="create-variants",
        permissions=["generate"],
        dialog={
            "title": "Варианты по цветам",
            "description": "Для каждого цвета создастся одноцветный вариант модели.",
            "form_class": ColorVariantsForm,
            "form_submit_text": "Создать",
        },
    )
    def create_variants(self, request, form, object_id):
        model = form.model_obj
        try:
            variants, products, skipped = generators.create_color_variants(
                model,
                form.cleaned_data["material"],
                list(form.cleaned_data["colors"]),
                add_random=form.cleaned_data["add_random"],
                create_products=form.cleaned_data["create_products"],
            )
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            if variants:
                messages.success(
                    request,
                    f"Создано вариантов: {len(variants)}, поштучных товаров: {len(products)}.",
                )
            if skipped:
                messages.warning(request, "Уже есть, пропущено: " + ", ".join(skipped) + ".")
        return hx_redirect(request, _admin_url(model))

    @action(
        description="Создать набор",
        icon="inventory_2",
        url_path="create-set",
        permissions=["generate"],
        dialog={
            "title": "Набор из нескольких штук",
            "form_class": SetForm,
            "form_submit_text": "Создать",
        },
    )
    def create_set(self, request, form, object_id):
        model = form.model_obj
        try:
            product = generators.create_set(
                model,
                list(form.cleaned_data["variants"]),
                form.cleaned_data["total"],
                packaging=form.cleaned_data["packaging"],
                packing_minutes=form.cleaned_data["packing_minutes"],
            )
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
            return hx_redirect(request, _admin_url(model))
        messages.success(request, f"Создан набор {product.sku}.")
        return hx_redirect(request, _admin_url(product))

    @action(
        description="Создать размер",
        icon="straighten",
        url_path="create-size",
        permissions=["generate_size"],
        dialog={
            "title": "Новый размер модели",
            "description": "Скопируется состав и варианты. Затем поменяйте детали и количества.",
            "form_class": SizeForm,
            "form_submit_text": "Создать",
        },
    )
    def create_size(self, request, form, object_id):
        model = ProductModel.objects.get(pk=object_id)
        try:
            copy = generators.create_size(model, form.cleaned_data["size"])
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
            return hx_redirect(request, _admin_url(model))
        messages.success(request, f"Создана модель «{copy.name}».")
        return hx_redirect(request, _admin_url(copy))


# --- Варианты ----------------------------------------------------------------------


class VariantSlotForm(ActiveChoicesForm):
    """
    Материал — из категории детали слота; цвет — из палитр всех этих материалов,
    чтобы материал и цвет можно было сменить за одно сохранение. Что цвет есть именно
    у выбранного материала, проверяет VariantSlot.clean().
    """

    class Meta:
        model = VariantSlot
        fields = ("material", "color")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        slot = self.instance
        if not slot.pk:
            return
        default = slot.variant.model.slot_default_materials().get(slot.slot)
        materials = self.fields["material"].queryset
        if default:
            materials = materials.filter(category_id=default.category_id)
            self.fields["material"].queryset = materials
        self.fields["color"].queryset = Color.objects.filter(
            is_active=True, materials__in=materials
        ).distinct()
        self.fields["color"].help_text = "Цвет должен быть в палитре выбранного материала."


class VariantSlotInline(LineInline):
    model = VariantSlot
    form = VariantSlotForm
    can_delete = False
    fields = ("slot", "material", "color", "swatch")
    readonly_fields = ("slot", "swatch")
    verbose_name_plural = "Цвета по слотам"

    def has_add_permission(self, request, obj=None):
        return False

    @display(description="")
    def swatch(self, obj):
        return color_swatch(obj.color.hex, obj.color.name) if obj.color_id else ""


@admin.register(Variant)
class VariantAdmin(PhotoUploaderMixin, CostVisibilityMixin, HistoryAdmin, ArchiveAdmin):
    list_display = ("swatches", "__str__", "code", "coloring", "cost_display", "is_active")
    list_display_links = ("swatches", "__str__")
    list_filter = ("coloring", "model")
    search_fields = ("name", "code", "model__name", "model__code")
    autocomplete_fields = ("model",)
    inlines = (VariantSlotInline, VariantPhotoInline)
    readonly_fields = ("sku_display", "cost_tree")
    cost_fields = ("cost_display", "cost_tree")
    fieldsets = (
        (
            "Основное",
            {
                "classes": ["tab"],
                "fields": (
                    "model",
                    "name",
                    "code",
                    "sku_display",
                    "coloring",
                    "card_warning",
                    "is_active",
                    "notes",
                ),
            },
        ),
        ("Себестоимость", {"classes": ["tab"], "fields": ("cost_tree",)}),
    )

    def get_queryset(self, request):
        return (
            super().get_queryset(request).select_related("model").prefetch_related("slots__color")
        )

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj:
            fields.append("model")  # вариант не переносится в другую модель
        return fields

    def get_changeform_initial_data(self, request):
        return {"model": request.GET.get("model")}

    def response_add(self, request, obj, post_url_continue=None):
        if obj.model.slot_names():
            messages.info(request, "Выберите цвета по слотам на вкладке «Цвета по слотам».")
            return hx_redirect(request, _admin_url(obj))
        return super().response_add(request, obj, post_url_continue)

    @display(description="")
    def swatches(self, obj):
        if obj.coloring == Coloring.RANDOM:
            return "🎲"
        items = [color_swatch(s.color.hex, s.color.name) for s in obj.slots.all() if s.color]
        return format_html("".join(["{}"] * len(items)), *items) if items else "—"

    @display(description="Артикул поштучного товара")
    def sku_display(self, obj):
        return generators.single_product_sku(obj) if obj.pk else "—"

    @display(description="Себестоимость", ordering="cost_cached")
    def cost_display(self, obj):
        return fmt_money(obj.cost_cached)

    @display(description="Разбивка")
    def cost_tree(self, obj):
        return render_cost_tree(variant_tree(obj)) if obj.pk else "Посчитается после сохранения"
