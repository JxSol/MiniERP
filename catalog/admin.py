from decimal import Decimal

from django import forms
from django.contrib import admin
from django.db.models import Avg, DecimalField, ExpressionWrapper, F, Q
from django.utils.html import format_html, format_html_join, mark_safe
from unfold.decorators import display
from unfold.widgets import UnfoldAdminSelect2MultipleWidget, UnfoldAdminTextInputWidget

from core.admin import ArchiveAdmin, HistoryAdmin, LineInline
from core.admin_utils import (
    color_swatch,
    fmt_duration,
    fmt_money,
    fmt_number,
    fmt_price,
    fmt_quantity,
)
from core.forms import ActiveChoicesForm

from .models import (
    FORM_UNITS,
    Color,
    Equipment,
    Manufacturer,
    Material,
    MaterialCategory,
    MaterialForm,
    MaterialOffer,
    MaterialPriceHistory,
    OperationType,
    Supplier,
    next_free_cipher,
)

# --- Категории материалов ----------------------------------------------------------


USERS_SHOWN = 5  # сколько мест использования связи перечислять в сообщении


class MaterialCategoryForm(forms.ModelForm):
    # Поле объявлено явно: список с поиском и крестиками, без кнопки «+» у связи на себя
    interchangeable = MaterialCategory._meta.get_field("interchangeable").formfield(
        widget=UnfoldAdminSelect2MultipleWidget(attrs={"style": "width: 100%"})
    )

    class Meta:
        model = MaterialCategory
        fields = ("name", "parent", "kind", "interchangeable", "is_active", "notes")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].required = False
        # У новой категории вид ещё не выбран — предлагаем все, вид сверяется при сохранении
        choices = MaterialCategory.objects.filter(is_active=True)
        if self.instance.pk:
            linked = self.instance.interchangeable.values("pk")
            choices = MaterialCategory.objects.filter(
                Q(is_active=True, kind=self.instance.kind) | Q(pk__in=linked)
            ).exclude(pk=self.instance.pk)
        self.fields["interchangeable"].queryset = choices

    def clean(self):
        data = super().clean()
        parent = data.get("parent")
        if parent:
            data["kind"] = parent.kind
            self.instance.kind = parent.kind
        elif not data.get("kind"):
            self.add_error("kind", "У категории верхнего уровня нужно выбрать вид материала.")
        self._check_interchangeable(data)
        return data

    def _check_interchangeable(self, data):
        chosen = list(data.get("interchangeable") or [])
        kind = data.get("kind")
        wrong = [f"«{category}»" for category in chosen if kind and category.kind != kind]
        if wrong:
            self.add_error(
                "interchangeable",
                "Взаимозаменяемыми бывают только категории одного вида материала. "
                f"Не подходят: {', '.join(wrong)}.",
            )
        if not self.instance.pk:
            return
        # Связь, на которой уже держатся варианты и модели, убрать нельзя
        from production.services import interchange_users

        removed = self.instance.interchangeable.exclude(pk__in=[c.pk for c in chosen])
        for other in removed:
            users = interchange_users(self.instance.pk, other.pk)
            if not users:
                continue
            shown = ", ".join(users[:USERS_SHOWN])
            if len(users) > USERS_SHOWN:
                shown += f" и ещё {len(users) - USERS_SHOWN}"
            self.add_error(
                "interchangeable",
                f"Связь с «{other}» убрать нельзя, она используется: {shown}. "
                "Сначала поменяйте там материал.",
            )


@admin.register(MaterialCategory)
class MaterialCategoryAdmin(ArchiveAdmin):
    form = MaterialCategoryForm
    list_display = ("path", "kind", "interchangeable_display", "is_active")
    list_filter = ("kind",)
    search_fields = ("name", "path")
    ordering = ("path",)

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("interchangeable")

    @display(description="Взаимозаменяема с")
    def interchangeable_display(self, obj):
        return ", ".join(category.name for category in obj.interchangeable.all()) or "—"


# --- Палитра, производители, поставщики ---------------------------------------------


class ColorHexWidget(UnfoldAdminTextInputWidget):
    """
    Код цвета #RRGGBB, который можно вписать или вставить, плюс рядом выбор цвета браузера
    (с палитрой и пипеткой). Оба поля связаны: что поменяли в одном, видно в другом.
    """

    def __init__(self, attrs=None):
        super().__init__(
            {
                "maxlength": "7",
                "placeholder": "#388E3C",
                "spellcheck": "false",
                "style": "width:9rem;font-family:monospace;text-transform:uppercase",
                **(attrs or {}),
            }
        )

    def render(self, name, value, attrs=None, renderer=None):
        text_html = super().render(name, value, attrs, renderer)
        picker_value = value if value and str(value).startswith("#") else "#888888"
        return format_html(
            '<div class="flex items-center gap-2">'
            '<input type="color" value="{}" title="Выбрать цвет или взять пипеткой" '
            'aria-label="Выбрать цвет" '
            'class="h-[38px] w-12 cursor-pointer rounded-default border border-base-200 '
            'bg-white p-1 dark:border-base-700 dark:bg-base-900" '
            "oninput=\"const t = this.parentElement.querySelector('input[type=text]'); "
            "t.value = this.value.toUpperCase(); t.dispatchEvent(new Event('input', {{bubbles: true}}));\">"
            "{}"
            "</div>"
            "<script>(function () {{ const box = document.currentScript.previousElementSibling; "
            "const text = box.querySelector('input[type=text]'); "
            "const picker = box.querySelector('input[type=color]'); "
            "text.addEventListener('input', () => {{ let v = text.value.trim(); "
            "if (/^[0-9a-fA-F]{{6}}$/.test(v)) v = '#' + v; "
            "if (/^#[0-9a-fA-F]{{6}}$/.test(v)) picker.value = v.toLowerCase(); }}); }})();</script>",
            picker_value,
            text_html,
        )


def normalize_hex(value: str) -> str:
    """«388e3c», «#38c», « #388E3C » → «#388E3C»; остальное возвращается как есть."""
    text = (value or "").strip().upper().replace("＃", "#")
    if text and not text.startswith("#"):
        text = "#" + text
    if len(text) == 4 and all(c in "0123456789ABCDEF" for c in text[1:]):
        text = "#" + "".join(c * 2 for c in text[1:])
    return text


class ColorForm(forms.ModelForm):
    class Meta:
        model = Color
        fields = ("name", "hex", "order", "is_active", "notes")
        widgets = {"hex": ColorHexWidget}

    def clean_hex(self):
        return normalize_hex(self.cleaned_data.get("hex", ""))


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


class CipherWidget(UnfoldAdminTextInputWidget):
    """Поле шифра с кнопкой «Подставить»: ближайший свободный шифр одним нажатием."""

    def __init__(self, free: str = "", attrs=None):
        super().__init__(attrs)
        self.free = free

    def render(self, name, value, attrs=None, renderer=None):
        field_html = super().render(name, value, attrs, renderer)
        if not self.free:
            return field_html
        return format_html(
            '<div class="flex items-center gap-2">{}'
            '<button type="button" data-cipher="{}" title="Ближайший свободный шифр" '
            "onclick=\"const i = this.parentElement.querySelector('input'); "
            "i.value = this.dataset.cipher; i.dispatchEvent(new Event('input', {{bubbles: true}}));\" "
            'class="border border-base-200 rounded-default h-[38px] px-3 text-sm font-medium '
            "whitespace-nowrap hover:text-primary-600 dark:border-base-700 "
            'dark:hover:text-primary-500">Подставить {}</button></div>',
            field_html,
            self.free,
            self.free,
        )


class MaterialAdminForm(ActiveChoicesForm):
    """Единица расхода нужна только обычной форме: остальным её задаёт форма учёта."""

    class Meta:
        model = Material
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].required = False
        free = next_free_cipher()
        if free and not self.instance.cipher:
            cipher = self.fields["cipher"]
            cipher.widget = CipherWidget(free=free, attrs=cipher.widget.attrs)
            cipher.help_text = (
                "Необязательно: впишите свой или нажмите «Подставить». Пишется на образцах "
                "вместо названия, чтобы посторонние не узнали материал."
            )

    def clean(self):
        data = super().clean()
        form_type = data.get("form_type")
        if form_type in FORM_UNITS:
            data["unit"] = FORM_UNITS[form_type]
        elif not data.get("unit"):
            self.add_error("unit", "Укажите единицу расхода.")
        return data


class MaterialOfferForm(ActiveChoicesForm):
    """Для рулона упаковка = площадь рулона, подставляется из размеров материала."""

    class Meta:
        model = MaterialOffer
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["pack_size"].required = False

    def clean(self):
        data = super().clean()
        if not data.get("pack_size"):
            material = getattr(self.instance, "material", None)
            is_roll = material is not None and material.form_type == MaterialForm.ROLL
            if is_roll and material.roll_length_m:
                data["pack_size"] = material.roll_length_m  # рулон: упаковка = его длина
            else:
                self.add_error("pack_size", "Укажите, сколько единиц расхода в упаковке.")
        return data


class MaterialOfferInline(LineInline):
    model = MaterialOffer
    form = MaterialOfferForm
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
        return price_text(obj.material, obj.unit_price)


def price_text(material, unit_price) -> str:
    """«60 ₸/лист · 178,36 ₸/м²»."""
    parts = material.price_breakdown(unit_price)
    return " · ".join(f"{fmt_price(p)}/{u}" for p, u in parts) if parts else "—"


class PriceHistoryInline(LineInline):
    model = MaterialPriceHistory
    can_delete = False
    fields = (
        "changed_at",
        "offer_display",
        "pack_display",
        "pack_price_display",
        "unit_price_display",
        "changed_by",
    )
    readonly_fields = fields
    verbose_name_plural = "История цен"

    @display(description="Предложение")
    def offer_display(self, obj):
        return str(obj.offer)

    @display(description="Упаковка")
    def pack_display(self, obj):
        return obj.material.format_qty(obj.pack_size)

    @display(description="Цена упаковки")
    def pack_price_display(self, obj):
        return fmt_price(obj.pack_price)

    def has_add_permission(self, request, obj=None):
        return False

    @display(description="За единицу")
    def unit_price_display(self, obj):
        return fmt_price(obj.unit_price)


@admin.register(Material)
class MaterialAdmin(HistoryAdmin, ArchiveAdmin):
    form = MaterialAdminForm
    # Размеры показываются только для подходящей формы учёта (Alpine, без перезагрузки)
    conditional_fields = {
        "unit": "form_type === 'plain'",
        "width_cm": "['piece', 'sheet', 'roll'].includes(form_type)",
        "length_cm": "['piece', 'sheet'].includes(form_type)",
        "height_cm": "form_type === 'piece'",
        "roll_length_m": "form_type === 'roll'",
    }
    list_display = (
        "swatch",
        "full_name_display",
        "cipher",
        "category",
        "unit_price_display",
        "offers_display",
        "is_active",
    )
    list_display_links = ("swatch", "full_name_display")
    list_filter = ("category__kind", "category", "color")
    search_fields = (
        "name",
        "color__name",
        "cipher",
        "offers__manufacturer__name",
        "offers__supplier__name",
    )
    autocomplete_fields = ("category", "color")
    inlines = (MaterialOfferInline, PriceHistoryInline)
    readonly_fields = ("size_display", "unit_price_display", "where_used")
    fieldsets = (
        (
            "Данные",
            {
                "classes": ["tab"],
                "fields": (
                    "name",
                    "color",
                    "cipher",
                    "category",
                    "form_type",
                    "unit",
                    ("width_cm", "length_cm", "height_cm", "roll_length_m"),
                    "size_display",
                    "unit_price_display",
                    "min_stock",
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
        return price_text(obj, price)

    @display(description="Расчёт по размерам")
    def size_display(self, obj):
        """Площадь, объём, площадь рулона; пересчитывается на лету из полей формы (Alpine)."""
        js = "((n) => n.toFixed(4).replace(/(\\.\\d*?)0+$/, '$1').replace(/\\.$/, '').replace('.', ','))"
        area = "((+width_cm || 0) * (+length_cm || 0)) / 10000"
        volume = "((+width_cm || 0) * (+length_cm || 0) * (+height_cm || 0)) / 1000000"
        roll = "((+width_cm || 0) / 100) * (+roll_length_m || 0)"
        vol = obj.piece_volume_m3 or Decimal(0)
        return format_html(
            "<div x-show=\"form_type === 'sheet'\">Площадь листа: "
            '<b x-text="{js}({area})">{a}</b> м²</div>'
            "<div x-show=\"form_type === 'piece'\">Объём: "
            '<b x-text="{js}({volume})">{v}</b> м³ (<b x-text="{js}({volume} * 1000)">{l}</b> л)</div>'
            "<div x-show=\"form_type === 'roll'\">Рулон: "
            '<b x-text="{js}({roll})">{r}</b> м² = <b x-text="{js}(+roll_length_m || 0)">{rl}</b> пог. м</div>'
            "<div x-show=\"form_type === 'plain' || form_type === 'length'\" class=\"text-base-500\">"
            "Размеры не нужны</div>",
            js=js,
            area=area,
            volume=volume,
            roll=roll,
            a=fmt_number(obj.sheet_area_m2 or 0),
            v=fmt_number(vol),
            l=fmt_number(vol * 1000),
            r=fmt_number(obj.roll_area_m2 or 0),
            rl=fmt_number(obj.roll_length_m or 0),
        )

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
                    f"{fmt_quantity(o.pack_size, obj.get_unit_display())} "
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
        from stock.reports import fact_scrap_percent

        source = "свой" if obj.scrap_percent is not None else "из общих настроек"
        planned = f"{fmt_number(obj.effective_scrap_percent)} % ({source})"
        fact = fact_scrap_percent(equipment=obj) if obj.pk else None
        if fact is None:
            return planned
        return f"{planned}; факт по выпускам: {fmt_number(fact.quantize(Decimal('0.1')))} %"


# --- Типы операций -----------------------------------------------------------------


@admin.register(OperationType)
class OperationTypeAdmin(HistoryAdmin, ArchiveAdmin):
    list_display = ("name", "level", "rate_display", "minutes_display", "order", "is_active")
    list_filter = ("level",)
    list_editable = ("order",)
    search_fields = ("name",)
    fields = ("name", "level", "rate", "default_minutes", "order", "is_active", "notes")

    @display(description="Время по умолчанию", ordering="default_minutes")
    def minutes_display(self, obj):
        return fmt_duration(obj.default_minutes) if obj.default_minutes else "—"

    @display(description="Ставка, ₸/ч")
    def rate_display(self, obj):
        if obj.rate is None:
            return f"{fmt_money(obj.effective_rate())} (из настроек)"
        return fmt_money(obj.rate)
