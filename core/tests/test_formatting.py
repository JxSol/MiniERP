"""Форматирование количеств и поле длительности."""

from decimal import Decimal as D

import pytest

from core.admin_utils import fmt_duration, fmt_money, fmt_number, fmt_price, fmt_quantity
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (D("7988.6634"), f"7{NBSP}988,66{NBSP}₸"),
        (D("90"), f"90,00{NBSP}₸"),  # сумма всегда с тиынами
        (D("4.585"), f"4,59{NBSP}₸"),  # ровно половина — вверх
        (D("0.01"), f"0,01{NBSP}₸"),
        # меньше одного тиына — до первой значащей цифры, чтобы сумма не стала нулём
        (D("0.004"), f"0,004{NBSP}₸"),
        (D("0.0096"), f"0,01{NBSP}₸"),
        (D("0.0003"), f"0,0003{NBSP}₸"),
        (D("0.00004"), f"<{NBSP}0,0001{NBSP}₸"),  # мельче 0,0001 не показываем
        (D("0"), f"0,00{NBSP}₸"),
        (D("-0.001"), f"0,00{NBSP}₸"),  # без «−0,00»
        (D("-12.345"), f"-12,35{NBSP}₸"),
        (None, "—"),
    ],
)
def test_fmt_money(value, expected):
    assert fmt_money(value) == expected


def test_fmt_money_whole_tenge():
    assert fmt_money(D("35652"), 0) == f"35{NBSP}652{NBSP}₸"
    assert fmt_money(D("4588.50"), 0) == f"4{NBSP}589{NBSP}₸"
    assert fmt_money(D("0.4"), 0) == f"0,4{NBSP}₸"  # меньше тенге — не «0 ₸»


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (D("4.588"), f"4,59{NBSP}₸"),  # катушка 1000 г за 4 588 ₸: не больше двух знаков
        (D("4.5000"), f"4,5{NBSP}₸"),  # без лишних нулей
        (D("15"), f"15{NBSP}₸"),
        (D("4588.00"), f"4{NBSP}588{NBSP}₸"),
        (D("178.3591"), f"178,36{NBSP}₸"),
        (D("0.0375"), f"0,04{NBSP}₸"),
        # меньше одного тиына — до первой значащей цифры
        (D("0.005"), f"0,005{NBSP}₸"),
        (D("0.0037"), f"0,004{NBSP}₸"),
        (D("0.00096"), f"0,001{NBSP}₸"),
        (D("0.0001"), f"0,0001{NBSP}₸"),
        (D("0.00001"), f"<{NBSP}0,0001{NBSP}₸"),
        (D("0"), f"0{NBSP}₸"),
        (None, "—"),
    ],
)
def test_fmt_price(value, expected):
    assert fmt_price(value) == expected


def test_tiny_and_zero_values():
    assert fmt_number(D("1000.0000")) == f"1{NBSP}000"
    assert fmt_number(D("0.00004")) == f"<{NBSP}0,0001"
    assert fmt_quantity(D("33.33333333333333333"), "г") == f"33,3333{NBSP}г"
    assert fmt_duration(D("59.97")) == "1 ч"
