"""
Поле длительности: дни / часы / минуты / секунды, в базе хранится как минуты.
Переполнение (1 ч 70 мин) нормализуется и в браузере (при потере фокуса),
и на сервере (2 ч 10 мин).
"""

from decimal import ROUND_HALF_UP, Decimal

from django import forms
from unfold.widgets import INPUT_CLASSES

PARTS = (
    ("days", "д", 1440),
    ("hours", "ч", 60),
    ("minutes", "мин", 1),
    ("seconds", "с", Decimal(1) / 60),
)


def minutes_to_parts(minutes: Decimal | None) -> list[int | None]:
    if minutes is None:
        return [None, None, None, None]
    total_seconds = int((Decimal(minutes) * 60).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    days, rest = divmod(total_seconds, 86400)
    hours, rest = divmod(rest, 3600)
    mins, secs = divmod(rest, 60)
    return [days, hours, mins, secs]


def parts_to_minutes(days, hours, mins, secs) -> Decimal:
    total_seconds = (days or 0) * 86400 + (hours or 0) * 3600 + (mins or 0) * 60 + (secs or 0)
    return (Decimal(total_seconds) / 60).quantize(Decimal("0.0001"))


class DurationWidget(forms.MultiWidget):
    template_name = "core/duration_widget.html"

    class Media:
        js = ("core/duration_widget.js",)

    def __init__(self, attrs=None):
        widgets = [
            forms.NumberInput(
                attrs={
                    "class": " ".join(INPUT_CLASSES) + " duration-part",
                    "min": "0",
                    "step": "1",
                    "inputmode": "numeric",
                    "data-part": key,
                    "placeholder": "0",
                    "style": "width:5.5rem",
                }
            )
            for key, _, _ in PARTS
        ]
        super().__init__(widgets, attrs)

    def decompress(self, value):
        return minutes_to_parts(value)

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        for subwidget, (_, label, _) in zip(context["widget"]["subwidgets"], PARTS, strict=True):
            subwidget["unit"] = label
        return context


class DurationMinutesField(forms.MultiValueField):
    widget = DurationWidget

    def __init__(self, *, min_minutes: Decimal = Decimal("0.0167"), **kwargs):
        fields = [forms.IntegerField(min_value=0, required=False) for _ in PARTS]
        kwargs.setdefault("require_all_fields", False)
        kwargs.setdefault("error_messages", {"required": "Укажите время."})
        super().__init__(fields, **kwargs)
        self.min_minutes = min_minutes

    def compress(self, data_list):
        if not data_list or all(v in (None, "") for v in data_list):
            if self.required:
                raise forms.ValidationError("Укажите время.")
            return None
        minutes = parts_to_minutes(*data_list)
        if minutes < self.min_minutes:
            raise forms.ValidationError("Время должно быть больше нуля.")
        return minutes
