"""
Общие помощники для админки: форматирование денег, дерево себестоимости,
скрытие себестоимости от ролей без права sales.view_cost.
"""

from contextvars import ContextVar
from decimal import ROUND_HALF_UP, Decimal

from django.http import HttpResponse
from django.shortcuts import redirect
from django.utils.html import format_html, format_html_join, mark_safe

COST_PERMISSION = "sales.view_cost"


# Текущий запрос — для вычисляемых полей, которым Django передаёт только объект
current_request: ContextVar = ContextVar("current_request", default=None)


def can_view_cost(request=None) -> bool:
    request = request or current_request.get()
    return bool(request and request.user.has_perm(COST_PERMISSION))


def fmt_money(value: Decimal | None, digits: int = 2) -> str:
    """7988.6634 → «7 988,66 ₸» (неразрывные пробелы между разрядами)."""
    if value is None:
        return "—"
    value = Decimal(value)
    unit = Decimal(1).scaleb(-digits)
    quantized = value.quantize(unit, rounding=ROUND_HALF_UP)
    if quantized == 0 and value > 0:
        return "< " + fmt_money(unit, digits)  # «< 0,01 ₸»: мало, но не бесплатно
    if quantized == 0:
        quantized = Decimal(0).quantize(unit)  # без «−0,00»
    text = f"{quantized:,.{digits}f}".replace(",", " ").replace(".", ",")
    return f"{text} ₸"


def fmt_price(value: Decimal | None) -> str:
    """Цена за единицу: без лишних нулей — «15 ₸», «9 ₸», «0,0375 ₸» (до 4 знаков)."""
    if value is None:
        return "—"
    quantized = Decimal(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    if quantized == 0 and Decimal(value) > 0:
        return "< " + fmt_money(Decimal("0.0001"), 4)
    text = f"{quantized:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    digits = len(text.split(".")[1]) if "." in text else 0
    return fmt_money(quantized, digits)


def fmt_quantity(value: Decimal | None, unit: str = "шт") -> str:
    """
    Количество с единицей: 2200 г → «2,2 кг», 1500 мл → «1,5 л», 12.5 шт → «12,5 шт».
    Разряды разделяются неразрывным пробелом.
    """
    if value is None:
        return "—"
    value = Decimal(value)
    if unit == "г" and abs(value) >= 1000:
        value, unit = (value / 1000).quantize(Decimal("0.001")), "кг"
    elif unit == "мл" and abs(value) >= 1000:
        value, unit = (value / 1000).quantize(Decimal("0.001")), "л"
    return f"{fmt_number(value)} {unit}"


def fmt_duration(minutes: Decimal | None) -> str:
    """480 → «8 ч», 90 → «1 ч 30 мин», 12.5 → «12,5 мин»."""
    if minutes is None:
        return "—"
    minutes = Decimal(minutes).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    hours, rest = divmod(minutes, 60)
    rest = rest.normalize()
    if hours and rest:
        return f"{int(hours)} ч {rest:f} мин".replace(".", ",")
    if hours:
        return f"{int(hours)} ч"
    return f"{rest:f} мин".replace(".", ",")


def fmt_number(value: Decimal | None) -> str:
    """Число без лишних нулей, не больше 4 знаков после запятой, разряды через неразрывный пробел."""
    if value is None:
        return "—"
    quantized = Decimal(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    if quantized == 0 and Decimal(value) > 0:
        return "< 0,0001"
    if quantized == 0:
        quantized = Decimal(0)
    text = f"{quantized.normalize():,f}"
    return text.replace(",", " ").replace(".", ",")


def color_swatch(color, title: str | None = None) -> str:
    """
    Кружок-образец цвета палитры: у обычного один цвет, у составного — секторы,
    у «Радуги» — спектр. Подсказка при наведении — название цвета или title.
    """
    return format_html(
        '<span title="{}" style="display:inline-block;width:14px;height:14px;border-radius:50%;'
        'background:{};border:1px solid rgba(0,0,0,.25);vertical-align:middle;margin-right:4px">'
        "</span>",
        color.name if title is None else title,
        color.swatch_css,
    )


def thumbnail(url: str, size: int = 48) -> str:
    if not url:
        return "—"
    return format_html(
        '<img src="{}" style="width:{}px;height:{}px;object-fit:cover;border-radius:6px">',
        url,
        size,
        size,
    )


def photo_square(photo, size: int = 240, empty: str = "Нет фото") -> str:
    """Главное фото квадратом 1:1 (object-fit: cover) для первой вкладки карточки; только показ."""
    if photo is None:
        return format_html('<span class="text-base-500">{}</span>', empty)
    return format_html(
        '<img src="{}" alt="" style="width:{}px;height:{}px;object-fit:cover;'
        'border-radius:8px;display:block">',
        photo.preview_url,
        size,
        size,
    )


def hx_redirect(request, url: str) -> HttpResponse:
    """
    Переход после действия. Диалоги unfold отправляются через HTMX: ему нужен
    ответ 200 с заголовком HX-Redirect (на 302 HTMX подставит страницу внутрь диалога).
    """
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=200)
        response["HX-Redirect"] = url
        return response
    return redirect(url)


# --- Дерево себестоимости --------------------------------------------------------

KIND_LABELS = {
    "product": "Товар",
    "variant": "Вариант",
    "part": "Деталь",
    "material": "Материал",
    "machine": "Работа станка",
    "amortization": "",
    "electricity": "",
    "scrap": "Брак",
    "labor": "Труд",
    "component": "Покупное",
    "packaging": "Упаковка",
    "overhead": "Накладные",
}


def _qty(node) -> str:
    if node.quantity is None:
        return ""
    if node.unit == "ч":
        return fmt_duration(node.quantity * 60)
    return fmt_quantity(node.quantity.quantize(Decimal("0.0001")), node.unit)


def _row(node, depth: int) -> str:
    unit_cost = (
        fmt_price(node.unit_cost) + (f" / {node.unit}" if node.unit else "")
        if node.unit_cost is not None and node.kind not in ("labor",)
        else ""
    )
    if node.kind == "labor" and node.unit_cost is not None:
        unit_cost = fmt_money(node.unit_cost * 60) + " / ч"
    padding = 8 + depth * 22
    weight = "600" if depth == 0 else "400"
    return format_html(
        '<tr style="border-bottom:1px solid rgba(127,127,127,.15)">'
        '<td style="padding:6px 8px 6px {}px;font-weight:{}">{}</td>'
        '<td style="padding:6px 8px;opacity:.7">{}</td>'
        '<td style="padding:6px 8px;text-align:right;white-space:nowrap">{}</td>'
        '<td style="padding:6px 8px;text-align:right;white-space:nowrap;opacity:.7">{}</td>'
        '<td style="padding:6px 8px;text-align:right;white-space:nowrap;font-weight:{}">{}</td>'
        "</tr>",
        padding,
        weight,
        node.name,
        KIND_LABELS.get(node.kind, ""),
        _qty(node),
        unit_cost,
        weight,
        fmt_money(node.amount),
    )


def _rows(node, depth: int) -> list[str]:
    rows = [_row(node, depth)]
    for child in node.children:
        rows.extend(_rows(child, depth + 1))
    return rows


def render_cost_tree(node, *, footer: list[tuple[str, Decimal]] | None = None) -> str:
    """Таблица-дерево: каждая строка с отступом по уровню вложенности."""
    if node is None:
        return "—"
    if isinstance(node, Exception):
        return format_html(
            "<span style='color:#b45309'>Себестоимость не считается: {}</span>", node
        )
    footer_html = format_html_join(
        "",
        '<tr><td colspan="4" style="padding:8px;font-weight:600;text-align:right">{}</td>'
        '<td style="padding:8px;text-align:right;font-weight:600;white-space:nowrap">{}</td></tr>',
        ((label, fmt_money(value)) for label, value in (footer or [])),
    )
    header = mark_safe(
        '<thead><tr style="text-align:left;opacity:.6;font-size:12px">'
        '<th style="padding:6px 8px">Статья</th><th style="padding:6px 8px">Вид</th>'
        '<th style="padding:6px 8px;text-align:right">Кол-во</th>'
        '<th style="padding:6px 8px;text-align:right">Цена</th>'
        '<th style="padding:6px 8px;text-align:right">Сумма</th></tr></thead>'
    )
    return format_html(
        '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;'
        'font-size:13px">{}<tbody>{}{}</tbody></table>'
        '<p style="opacity:.6;font-size:12px;margin-top:6px">У строк с количеством вложенные '
        "строки раскрывают стоимость одной единицы. Суммы считаются точно и округляются только "
        "при показе.</p></div>",
        header,
        mark_safe("".join(_rows(node, 0))),
        footer_html,
    )


def safe_tree(build, obj):
    """Дерево себестоимости или понятная ошибка вместо страницы 500."""
    from costing.loader import CostError

    try:
        return build(obj)
    except CostError as error:
        return error


class PageHelpMixin:
    """Кнопка «О разделе» (i) над списком и над карточкой: окно с описанием раздела."""

    list_before_template = "admin/page_help.html"
    change_form_outer_before_template = "admin/page_help.html"

    def _help_context(self, request, extra_context):
        from core.help import help_for

        page_help = help_for(self.model)
        if page_help and not can_view_cost(request):
            # Продавцу не показываем пункты про себестоимость — он её не видит
            hidden = "себестоимост"
            page_help = {
                key: [i for i in value if hidden not in i.lower()]
                if isinstance(value, list)
                else value
                for key, value in page_help.items()
                if not (isinstance(value, str) and key != "title" and hidden in value.lower())
            }
        return {**(extra_context or {}), "page_help": page_help}

    def changelist_view(self, request, extra_context=None):
        return super().changelist_view(request, self._help_context(request, extra_context))

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        return super().changeform_view(
            request, object_id, form_url, self._help_context(request, extra_context)
        )


class CostVisibilityMixin:
    """
    Убирает поля и колонки себестоимости для ролей без права sales.view_cost.
    Скрытие на сервере: продавец не получит эти данные ни в форме, ни в списке.
    """

    cost_fields: tuple[str, ...] = ()

    # Поля модели, по которым нельзя фильтровать и сортировать без права на себестоимость
    cost_model_fields: tuple[str, ...] = ("cost_cached", "cost_direct_cached")

    def _with_request(self, request, view, *args, **kwargs):
        """Вычисляемые поля рендерятся после возврата из view, поэтому рендерим здесь же."""
        token = current_request.set(request)
        try:
            response = view(request, *args, **kwargs)
            if hasattr(response, "render") and callable(response.render):
                response.render()
            return response
        finally:
            current_request.reset(token)

    def changeform_view(self, request, *args, **kwargs):
        return self._with_request(request, super().changeform_view, *args, **kwargs)

    def changelist_view(self, request, *args, **kwargs):
        return self._with_request(request, super().changelist_view, *args, **kwargs)

    def lookup_allowed(self, lookup, value, request=None):
        """Без права на себестоимость запрещены фильтры вида ?cost_cached__gt= (утечка подбором)."""
        if lookup.split("__")[0] in self.cost_model_fields and not can_view_cost(request):
            return False
        return super().lookup_allowed(lookup, value, request)

    def get_sortable_by(self, request):
        sortable = super().get_sortable_by(request)
        if can_view_cost(request):
            return sortable
        return [
            f for f in sortable if f not in self.cost_fields and f not in self.cost_model_fields
        ]

    def get_list_display(self, request):
        fields = super().get_list_display(request)
        if can_view_cost(request):
            return fields
        return [f for f in fields if f not in self.cost_fields]

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if can_view_cost(request):
            return fieldsets
        cleaned = []
        for title, options in fieldsets:
            fields = [f for f in options.get("fields", ()) if f not in self.cost_fields]
            if fields:
                cleaned.append((title, {**options, "fields": fields}))
        return cleaned

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        if can_view_cost(request):
            return fields
        return [f for f in fields if f not in self.cost_fields]

    def get_inlines(self, request, obj):
        inlines = super().get_inlines(request, obj)
        if can_view_cost(request):
            return inlines
        return [i for i in inlines if not getattr(i, "shows_cost", False)]
