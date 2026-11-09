from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.options import IS_POPUP_VAR
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.forms.models import BaseInlineFormSet
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from unfold.admin import ModelAdmin
from unfold.decorators import action, display
from unfold.forms import BaseDialogForm
from unfold.widgets import (
    UnfoldAdminCheckboxSelectMultipleWidget,
    UnfoldAdminDecimalFieldWidget,
    UnfoldAdminIntegerFieldWidget,
    UnfoldAdminSelectWidget,
    UnfoldBooleanSwitchWidget,
)

from catalog.models import Equipment, Material, MaterialKind
from core.admin import ArchiveAdmin, HistoryAdmin, LineInline
from core.admin_utils import (
    CostVisibilityMixin,
    PageHelpMixin,
    can_view_cost,
    color_swatch,
    fmt_duration,
    fmt_money,
    fmt_number,
    fmt_quantity,
    hx_redirect,
    photo_square,
    render_cost_tree,
    safe_tree,
    thumbnail,
)
from core.forms import ActiveChoicesForm, QuantityUnitFormMixin
from core.models import Size
from core.widgets import DurationMinutesField
from costing.services import model_tree, part_tree, variant_tree
from gallery.admin import ModelPhotoInline, PartPhotoInline, PhotoUploaderMixin, VariantPhotoInline
from gallery.models import main_photo, main_photo_for_model, main_photo_for_variant

from . import generators, services
from .models import (
    Coloring,
    ModelComponent,
    ModelOperation,
    ModelPart,
    Part,
    PartColoring,
    PartMaterial,
    PartOperation,
    ProductCategory,
    ProductModel,
    Variant,
    VariantSlot,
    clean_slot,
    slot_conflicts,
)

DEFAULT_SLOT = "Основной"


def _admin_url(obj, action="change"):
    return reverse(f"admin:{obj._meta.app_label}_{obj._meta.model_name}_{action}", args=[obj.pk])


def _link(obj, text=None):
    return format_html('<a href="{}" class="text-primary-600">{}</a>', _admin_url(obj), text or obj)


# --- Категории изделий -------------------------------------------------------------


@admin.register(ProductCategory)
class ProductCategoryAdmin(ArchiveAdmin):
    list_display = ("path", "code", "is_active")
    search_fields = ("name", "path", "code")
    fields = ("name", "parent", "code", "is_active", "notes")
    ordering = ("path",)


# --- Детали ------------------------------------------------------------------------


class CopyPartForm(BaseDialogForm):
    equipment = forms.ModelChoiceField(
        label="Станок для копии",
        queryset=Equipment.objects.filter(is_active=True),
        widget=UnfoldAdminSelectWidget,
        required=False,
        help_text="Пусто — тот же станок, что у исходной детали.",
    )


class PartForm(ActiveChoicesForm):
    batch_minutes = DurationMinutesField(
        label="Время типовой партии",
        help_text="Сколько идёт один стол или один лист — как показывает слайсер или программа лазера.",
    )

    class Meta:
        model = Part
        fields = "__all__"


class PartMaterialForm(QuantityUnitFormMixin, ActiveChoicesForm):
    quantity_field = "batch_qty"
    unit_field = "batch_unit"

    class Meta:
        model = PartMaterial
        fields = ("name", "material", "batch_qty", "batch_unit", "slot")


class PartMaterialFormSet(BaseInlineFormSet):
    """Первая позиция новой детали сразу в группе «Основной»; позиции нумеруются по порядку."""

    def __init__(self, *args, **kwargs):
        instance = kwargs.get("instance")
        if instance is None or not instance.pk:
            kwargs.setdefault("initial", [{"slot": DEFAULT_SLOT}])
        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()
        order = 0
        for form in self.forms:
            if not getattr(form, "cleaned_data", None):
                continue
            if form.cleaned_data.get("DELETE"):
                if form.instance.pk and form.instance.coloring_items.exists():
                    raise ValidationError(
                        f"Позицию «{form.instance.label}» удалить нельзя: из неё уже делали "
                        "детали, она есть в остатках и истории."
                    )
                continue
            order += 1
            form.instance.order = order


class PartMaterialInline(LineInline):
    model = PartMaterial
    form = PartMaterialForm
    formset = PartMaterialFormSet
    autocomplete_fields = ("material",)
    fields = ("name", "material", "batch_qty", "batch_unit", "slot", "per_piece_display")
    readonly_fields = ("per_piece_display",)
    min_num = 1
    validate_min = True
    tab = False
    verbose_name = "Материал"
    verbose_name_plural = "Материалы на типовую партию"

    @display(description="На 1 шт")
    def per_piece_display(self, obj):
        if not obj.pk:
            return "—"
        return fmt_quantity(
            obj.per_piece.quantize(Decimal("0.01")), obj.material.get_unit_display()
        )


class PartOperationInline(LineInline):
    model = PartOperation
    fields = ("operation_type", "minutes", "rate")
    verbose_name_plural = "Обработка"


@admin.register(Part)
class PartAdmin(PhotoUploaderMixin, CostVisibilityMixin, HistoryAdmin, ArchiveAdmin):
    form = PartForm
    list_display = (
        "name",
        "code",
        "equipment",
        "material_display",
        "per_piece_display",
        "cost_display",
        "is_active",
    )
    list_filter = ("equipment", "materials__material__category")
    search_fields = ("name", "code")
    autocomplete_fields = ("equipment",)
    inlines = (PartMaterialInline, PartOperationInline, PartPhotoInline)
    readonly_fields = (
        "code",
        "per_piece_display",
        "scrap_display",
        "cost_tree",
        "used_in",
        "colorings_display",
    )
    cost_fields = ("cost_display", "cost_tree")
    actions_detail = ("copy_part",)
    fieldsets = (
        (
            "Данные",
            {
                "classes": ["tab"],
                "fields": (
                    "name",
                    "code",
                    "equipment",
                    ("batch_size", "batch_minutes"),
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
        ("Где используется", {"classes": ["tab"], "fields": ("used_in", "colorings_display")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("materials__material__color")

    @display(description="Материал")
    def material_display(self, obj):
        positions = obj.positions
        if not positions:
            return "—"
        text = str(positions[0].material)
        return f"{text} + ещё {len(positions) - 1}" if len(positions) > 1 else text

    @display(description="На 1 шт")
    def per_piece_display(self, obj):
        if not obj.pk:
            return "Посчитается после сохранения"
        positions = obj.positions
        parts = []
        for position in positions:
            qty = fmt_quantity(
                position.per_piece.quantize(Decimal("0.01")), position.material.get_unit_display()
            )
            parts.append(f"{position.label}: {qty}" if len(positions) > 1 else qty)
        minutes = obj.batch_minutes / obj.batch_size
        return f"{' + '.join(parts)}, {fmt_duration(minutes)}"

    @display(description="Расцветки (из чего уже делали)")
    def colorings_display(self, obj):
        if not obj.pk:
            return "—"
        labels = list(obj.colorings.values_list("label", flat=True))
        return (
            format_html_join("", "<p>{}</p>", ((label,) for label in labels))
            or "Пока нет: появятся из вариантов и выпусков"
        )

    def save_related(self, request, form, formsets, change):
        """Переименование цветовой группы не должно стирать цвета вариантов (см. services)."""
        part = form.instance
        models = [line.model for line in part.model_lines.select_related("model")] if change else []
        before = {model.pk: services.snapshot_slots(model) for model in models}
        renames: dict[str, str] = {}
        for formset in formsets:
            if formset.model is not PartMaterial:
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
        for model in models:
            still_present = set(model.slot_names())
            pending = {o: n for o, n in renames.items() if o not in still_present}
            services.restore_renamed_slots(model, before[model.pk], pending)

    @display(description="% брака (итоговый)")
    def scrap_display(self, obj):
        if not obj.pk:
            return "—"
        from stock.reports import fact_scrap_percent

        if obj.scrap_percent is not None:
            planned = f"{fmt_number(obj.scrap_percent)} % (свой)"
        else:
            source = "из станка" if obj.equipment.scrap_percent is not None else "из общих настроек"
            planned = f"{fmt_number(obj.equipment.effective_scrap_percent)} % ({source})"
        fact = fact_scrap_percent(part=obj)
        if fact is None:
            return f"{planned}; факт: выпусков ещё не было"
        return f"{planned}; факт по выпускам: {fmt_number(fact.quantize(Decimal('0.1')))} %"

    @display(description="Себестоимость 1 шт", ordering="cost_cached")
    def cost_display(self, obj):
        return fmt_money(obj.cost_cached)

    @display(description="Разбивка")
    def cost_tree(self, obj):
        if not obj.pk:
            return "Посчитается после сохранения"
        return render_cost_tree(safe_tree(part_tree, obj))

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
            "description": "Создастся копия со всеми параметрами и операциями обработки. "
            "Другой станок — другая технология и новый код.",
            "form_class": CopyPartForm,
            "form_submit_text": "Копировать",
        },
    )
    def copy_part(self, request, form, object_id):
        part = get_object_or_404(Part, pk=object_id)
        copy = generators.copy_part(part, form.cleaned_data.get("equipment"))
        messages.success(request, f"Создана копия «{copy.name}». Поменяйте название и параметры.")
        return hx_redirect(request, _admin_url(copy))


# --- Модели ------------------------------------------------------------------------


class ModelPartFormSet(BaseInlineFormSet):
    """Детали одной цветовой группы должны быть из одной категории материалов."""

    def clean(self):
        super().clean()
        parts = [
            form.cleaned_data["part"]
            for form in self.forms
            if getattr(form, "cleaned_data", None)
            and not form.cleaned_data.get("DELETE")
            and form.cleaned_data.get("part")
        ]
        problems = slot_conflicts(parts)
        if problems:
            raise ValidationError(" ".join(problems))


class ModelPartInline(LineInline):
    model = ModelPart
    formset = ModelPartFormSet
    autocomplete_fields = ("part",)
    fields = ("part", "quantity", "groups_display")
    readonly_fields = ("groups_display",)
    verbose_name_plural = "Детали"

    @display(description="Цветовые группы")
    def groups_display(self, obj):
        if not obj.pk:
            return "—"
        names = obj.part.slot_names()
        return ", ".join(names) if names else "цвет не меняется"


class ModelComponentForm(QuantityUnitFormMixin, ActiveChoicesForm):
    class Meta:
        model = ModelComponent
        fields = ("material", "quantity", "entered_unit")


class ModelComponentInline(LineInline):
    model = ModelComponent
    form = ModelComponentForm
    autocomplete_fields = ("material",)
    fields = ("material", "quantity", "entered_unit")
    verbose_name_plural = "Покупное"


class ModelOperationInline(LineInline):
    model = ModelOperation
    fields = ("operation_type", "minutes", "rate")
    verbose_name_plural = "Сборка"


class MaterialChoiceField(forms.ModelMultipleChoiceField):
    """Галочки с образцами цвета (ТЗ п. 5.1)."""

    def label_from_instance(self, material):
        swatch = color_swatch(material.color.hex, material.color.name) if material.color_id else ""
        return format_html("{}{}", swatch, material.full_name)


class MaterialVariantsForm(BaseDialogForm):
    materials = MaterialChoiceField(
        label="Материалы",
        queryset=Material.objects.none(),
        widget=UnfoldAdminCheckboxSelectMultipleWidget,
        required=False,
        help_text="Для каждого отмеченного материала создастся вариант, названный по цвету.",
    )
    add_random = forms.BooleanField(
        label="Добавить вариант «Ассорти» (случайный цвет той же линейки)",
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
        self.model_obj = get_object_or_404(ProductModel, pk=object_id)
        categories = generators.slot_category_ids(self.model_obj)
        self.fields["materials"].queryset = (
            Material.objects.filter(is_active=True, category_id__in=categories)
            .select_related("color")
            .order_by("name", "color__order")
        )

    def clean(self):
        data = super().clean()
        if not data.get("materials"):
            self.add_error("materials", "Отметьте хотя бы один материал.")
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
        self.model_obj = get_object_or_404(ProductModel, pk=object_id)
        self.fields["variants"].queryset = self.model_obj.variants.filter(is_active=True)


class SizeForm(BaseDialogForm):
    size = forms.ModelChoiceField(
        label="Размер", queryset=Size.objects.none(), widget=UnfoldAdminSelectWidget
    )

    def __init__(self, request, object_id=None, *args, **kwargs):
        super().__init__(request, object_id, *args, **kwargs)
        model = get_object_or_404(ProductModel, pk=object_id)
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
    readonly_fields = (
        "code",
        "article_base",
        "main_photo_display",
        "calculated_weight_display",
        "slots_overview",
        "variants_overview",
        "products_overview",
        "sizes_overview",
        "cost_tree",
    )
    cost_fields = ("cost_display", "cost_tree")
    actions_detail = ("create_variants", "create_set", "create_size")
    fieldsets = (
        (
            "Данные",
            {
                "classes": ["tab"],
                "fields": (
                    "main_photo_display",
                    "name",
                    ("code", "article_base"),
                    "category",
                    "tags",
                    "status",
                    "slots_overview",
                    "description",
                    ("weight_g", "calculated_weight_display"),
                    ("length_cm", "width_cm", "height_cm"),
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
            .prefetch_related("variants__slots__material__color")
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

    @display(description="Основа артикула")
    def article_base(self, obj):
        if not obj.pk:
            return "появится после сохранения, если у категории есть код"
        return obj.article_base or "— (у категории нет кода для артикулов)"

    @display(description="Фото первого варианта")
    def main_photo_display(self, obj):
        return photo_square(main_photo_for_model(obj) if obj.pk else None)

    @display(description="Варианты")
    def variants_display(self, obj):
        swatches = []
        for variant in obj.variants.all():
            colors = [s.material.color for s in variant.slots.all() if s.material.color_id]
            if variant.coloring == Coloring.RANDOM:
                swatches.append(format_html('<span title="{}">🎲</span>', variant.name))
            for color in colors[:1]:
                swatches.append(color_swatch(color.hex, variant.name))
        return format_html("".join(["{}"] * len(swatches)), *swatches) if swatches else "—"

    @display(description="Себестоимость", ordering="cost_cached")
    def cost_display(self, obj):
        return fmt_money(obj.cost_cached)

    @display(description="Расчётная масса пластика по нормам деталей")
    def calculated_weight_display(self, obj):
        weight = obj.calculated_weight_g() if obj.pk else None
        return fmt_quantity(weight, "г") if weight is not None else "—"

    @display(description="Цветовые группы")
    def slots_overview(self, obj):
        if not obj.pk:
            return "Появятся после сохранения: собираются из деталей состава."
        positions = obj.slot_positions()
        if not positions:
            return "Нет: у всех деталей цвет фиксирован. Группа задаётся в карточке детали."
        rows = []
        for slot, pairs in positions.items():
            names = dict.fromkeys(
                f"{line.part.name} ({position.label})" if position.name else line.part.name
                for line, position in pairs
            )
            rows.append(format_html("<b>{}</b>: {}", slot, ", ".join(names)))
        return format_html("<br>".join(["{}"] * len(rows)), *rows)

    @display(description="Варианты модели")
    def variants_overview(self, obj):
        if not obj.pk:
            return "Сохраните модель, затем создайте варианты кнопкой «Создать варианты» вверху."
        rows = []
        show_cost = can_view_cost()
        for v in obj.variants.prefetch_related("slots__material__color"):
            slots = format_html_join(
                ", ",
                "{}{}",
                (
                    (
                        color_swatch(s.material.color.hex, s.material.color.name)
                        if s.material.color_id
                        else "",
                        f"{s.slot}: {s.material}",
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
                    v.full_code,
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
        if not obj.pk:
            return "Посчитается после сохранения"
        return render_cost_tree(safe_tree(model_tree, obj))

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
            "title": "Варианты по материалам",
            "description": "Для каждого материала создастся одноцветный вариант модели.",
            "form_class": MaterialVariantsForm,
            "form_submit_text": "Создать",
        },
    )
    def create_variants(self, request, form, object_id):
        model = form.model_obj
        try:
            variants, products, skipped = generators.create_material_variants(
                model,
                list(form.cleaned_data["materials"]),
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
        model = get_object_or_404(ProductModel, pk=object_id)
        try:
            copy = generators.create_size(model, form.cleaned_data["size"])
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
            return hx_redirect(request, _admin_url(model))
        messages.success(request, f"Создана модель «{copy.name}».")
        return hx_redirect(request, _admin_url(copy))


# --- Варианты ----------------------------------------------------------------------


class VariantSlotForm(ActiveChoicesForm):
    """Материал группы — только из категории детали по умолчанию."""

    class Meta:
        model = VariantSlot
        fields = ("material",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        slot = self.instance
        if not slot.pk:
            return
        default = slot.variant.model.slot_default_materials().get(slot.slot)
        if default:
            self.fields["material"].queryset = (
                self.fields["material"].queryset.filter(category_id=default.category_id)
            ).select_related("color")


class VariantSlotInline(LineInline):
    model = VariantSlot
    form = VariantSlotForm
    can_delete = False
    fields = ("slot", "material", "swatch")
    readonly_fields = ("slot", "swatch")
    verbose_name_plural = "Цвета по группам"

    def has_add_permission(self, request, obj=None):
        return False

    @display(description="")
    def swatch(self, obj):
        color = obj.material.color if obj.material_id else None
        return color_swatch(color.hex, color.name) if color else ""


@admin.register(Variant)
class VariantAdmin(PhotoUploaderMixin, CostVisibilityMixin, HistoryAdmin, ArchiveAdmin):
    list_display = (
        "photo",
        "swatches",
        "name",
        "model",
        "code",
        "coloring",
        "cost_display",
        "is_active",
    )
    list_display_links = ("photo", "swatches", "name")
    list_filter = ("coloring", "model")
    search_fields = ("name", "code", "model__name", "model__code")
    autocomplete_fields = ("model",)
    inlines = (VariantSlotInline, VariantPhotoInline)
    readonly_fields = ("full_code", "main_photo_display", "product_display", "cost_tree")
    cost_fields = ("cost_display", "cost_tree")
    fieldsets = (
        (
            "Данные",
            {
                "classes": ["tab"],
                "fields": (
                    "main_photo_display",
                    "model",
                    "name",
                    "full_code",
                    "product_display",
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
            super()
            .get_queryset(request)
            .select_related("model")
            .prefetch_related("slots__material__color")
        )

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj:
            fields.append("model")  # вариант не переносится в другую модель
        return fields

    @display(description="Главное фото")
    def main_photo_display(self, obj):
        return photo_square(main_photo_for_variant(obj) if obj.pk else None)

    def get_changeform_initial_data(self, request):
        return {"model": request.GET.get("model")}

    def response_add(self, request, obj, post_url_continue=None):
        if obj.model.slot_names():
            messages.info(request, "Выберите цвета на вкладке «Цвета по группам».")
            if IS_POPUP_VAR not in request.POST and "_addanother" not in request.POST:
                return hx_redirect(request, _admin_url(obj))
        return super().response_add(request, obj, post_url_continue)

    @display(description="")
    def photo(self, obj):
        p = main_photo(variant=obj) or main_photo(model=obj.model)
        return thumbnail(p.preview_url if p else "", 40)

    @display(description="")
    def swatches(self, obj):
        if obj.coloring == Coloring.RANDOM:
            return "🎲"
        items = [
            color_swatch(s.material.color.hex, s.material.color.name)
            for s in obj.slots.all()
            if s.material.color_id
        ]
        return format_html("".join(["{}"] * len(items)), *items) if items else "—"

    @display(description="Поштучный товар")
    def product_display(self, obj):
        if not obj.pk:
            return "—"
        product = generators.single_product_of(obj)
        if product is None:
            return "ещё не создан"
        return _link(product, f"{product.article or product.sku} — {product.name}")

    @display(description="Код")
    def full_code(self, obj):
        return obj.full_code if obj.pk else "присвоится при сохранении"

    @display(description="Себестоимость", ordering="cost_cached")
    def cost_display(self, obj):
        return fmt_money(obj.cost_cached)

    @display(description="Разбивка")
    def cost_tree(self, obj):
        if not obj.pk:
            return "Посчитается после сохранения"
        return render_cost_tree(safe_tree(variant_tree, obj))


# --- Расцветки деталей -------------------------------------------------------------


@admin.register(PartColoring)
class PartColoringAdmin(PageHelpMixin, ModelAdmin):
    """Ведёт система: нужен для выбора детали с её материалами в документах склада."""

    list_display = ("part", "label")
    list_filter = ("part",)
    search_fields = ("part__name", "part__code", "label")
    fields = ("part", "label")
    readonly_fields = ("part", "label")
    list_filter_submit = False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("part")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
