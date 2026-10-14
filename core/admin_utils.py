"""
Общие помощники для админки: форматирование денег, дерево себестоимости,
скрытие себестоимости от ролей без права sales.view_cost.
"""

from contextvars import ContextVar
from decimal import Decimal

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
    quantized = Decimal(value).quantize(Decimal(1).scaleb(-digits))
    text = f"{quantized:,.{digits}f}".replace(",", " ").replace(".", ",")
    return f"{text} ₸"


def fmt_number(value: Decimal | None) -> str:
    if value is None:
        return "—"
    text = f"{Decimal(value).normalize():f}"
    return text.replace(".", ",")


def color_swatch(hex_value: str, title: str = "") -> str:
    return format_html(
        '<span title="{}" style="display:inline-block;width:14px;height:14px;border-radius:50%;'
        'background:{};border:1px solid rgba(0,0,0,.25);vertical-align:middle;margin-right:4px">'
        "</span>",
        title,
        hex_value,
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
    return f"{fmt_number(node.quantity.quantize(Decimal('0.0001')))} {node.unit}"


def _row(node, depth: int) -> str:
    unit_cost = (
        fmt_money(node.unit_cost) + (f" / {node.unit}" if node.unit else "")
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


class CostVisibilityMixin:
    """
    Убирает поля и колонки себестоимости для ролей без права sales.view_cost.
    Скрытие на сервере: продавец не получит эти данные ни в форме, ни в списке.
    """

    cost_fields: tuple[str, ...] = ()

    def changeform_view(self, request, *args, **kwargs):
        token = current_request.set(request)
        try:
            return super().changeform_view(request, *args, **kwargs)
        finally:
            current_request.reset(token)

    def changelist_view(self, request, *args, **kwargs):
        token = current_request.set(request)
        try:
            return super().changelist_view(request, *args, **kwargs)
        finally:
            current_request.reset(token)

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
