"""Форматирование количеств и поле длительности."""

from decimal import Decimal as D

import pytest

from core.admin_utils import fmt_duration, fmt_quantity
from core.widgets import DurationMinutesField, minutes_to_parts, parts_to_minutes

NBSP = " "


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (D("2200"), "г", f"2,2{NBSP}кг"),
        (D("2250.0000"), "г", f"2,25{NBSP}кг"),
        (D("1000"), "г", f"1{NBSP}кг"),
        (D("250"), "г", f"250{NBSP}г"),
        (D("25000"), "г", f"25{NBSP}кг"),
        (D("1500"), "мл", f"1,5{NBSP}л"),
        (D("12.5"), "шт", f"12,5{NBSP}шт"),
        (D("1234567"), "шт", f"1{NBSP}234{NBSP}567{NBSP}шт"),
        (None, "г", "—"),
    ],
)
def test_fmt_quantity(value, unit, expected):
    assert fmt_quantity(value, unit) == expected


def test_duration_parts_roundtrip():
    assert minutes_to_parts(D(480)) == [0, 8, 0, 0]
    assert minutes_to_parts(D("1570.5")) == [1, 2, 10, 30]
    assert parts_to_minutes(0, 1, 70, 0) == D(130)  # 1 ч 70 мин = 2 ч 10 мин = 130 мин
    assert parts_to_minutes(0, 0, 0, 30) == D("0.5")
    assert minutes_to_parts(parts_to_minutes(2, 3, 4, 5)) == [2, 3, 4, 5]


def test_duration_field_compress_and_validation():
    field = DurationMinutesField()
    assert field.clean(["", "1", "70", ""]) == D(130)
    assert field.clean(["", "", "", "30"]) == D("0.5")
    with pytest.raises(Exception, match="больше нуля"):
        field.clean(["0", "0", "0", "0"])
    with pytest.raises(Exception, match="Укажите время"):
        field.clean(["", "", "", ""])


def test_fmt_duration():
    assert fmt_duration(D(480)) == "8 ч"
    assert fmt_duration(D(90)) == "1 ч 30 мин"
    assert fmt_duration(D("12.5")) == "12,5 мин"
