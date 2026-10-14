"""Начальные данные справочников (ТЗ, приложение А)."""

from django.db import migrations

from core.models import name_key

OPERATION_TYPES = [
    # (уровень, название, минут по умолчанию, системный ключ)
    ("part", "Снятие поддержек", None, None),
    ("part", "Удаление каймы и зачистка", None, None),
    ("part", "Шлифовка", None, None),
    ("part", "Грунтовка", None, None),
    ("part", "Покраска", None, None),
    ("part", "Лакировка", None, None),
    ("part", "Установка резьбовых втулок", None, None),
    ("part", "Снятие защитной плёнки", None, None),
    ("part", "Очистка от нагара", None, None),
    ("model", "Склейка", None, None),
    ("model", "Сборка", None, None),
    ("model", "Установка электрики", None, None),
    ("model", "Контроль качества", None, None),
    ("product", "Упаковка в фирменную коробку", 5, "pack"),
    ("product", "Комплектация набора", 10, "kit"),
    ("product", "Маркировка", 1, "label"),
    ("packing", "Доупаковка для отправки", 3, "ship-pack"),
]

MATERIAL_CATEGORIES = [
    ("Пластик для 3D-печати", "filament", ["PLA", "PETG", "ABS / ASA", "TPU (гибкий)"]),
    ("Смола (фотополимер)", "resin", []),
    ("Листовые материалы", "sheet", ["Фанера", "МДФ / ХДФ", "Акрил", "Прочие листовые"]),
    ("Комплектующие", "component", ["Электрика", "Крепёж", "Магниты", "Прочие комплектующие"]),
    ("Отделка", "finish", []),
    ("Упаковка", "packaging", ["Фирменная", "Транспортная"]),
    ("Расходники", "consumable", []),
]

COLORS = [
    ("Белый", "WH", "#FFFFFF"),
    ("Чёрный", "BK", "#1A1A1A"),
    ("Серый", "GY", "#8E8E8E"),
    ("Красный", "RD", "#D32F2F"),
    ("Оранжевый", "OR", "#F57C00"),
    ("Жёлтый", "YL", "#FBC02D"),
    ("Зелёный", "GN", "#388E3C"),
    ("Синий", "BL", "#1976D2"),
    ("Голубой", "LB", "#64B5F6"),
    ("Фиолетовый", "VT", "#7B1FA2"),
    ("Розовый", "PK", "#F48FB1"),
    ("Коричневый", "BR", "#6D4C41"),
    ("Бежевый", "BG", "#E8D9B5"),
    ("Мятный", "MT", "#A8E6CF"),
    ("Лавандовый", "LV", "#C5B3E6"),
    ("Персиковый", "PC", "#FFCBA4"),
    ("Золотой", "GD", "#D4AF37"),
    ("Серебряный", "SV", "#C0C0C0"),
    ("Бронзовый", "BZ", "#CD7F32"),
    ("Прозрачный", "CL", "#F0F8FF"),
    ("Натуральный", "NT", "#F3EFE0"),
]

SIZES = [("XS", "XS"), ("S", "S"), ("M", "M"), ("L", "L"), ("XL", "XL"), ("XXL", "XXL")]
TAGS = ["Новинка", "Хит", "Новый год", "Подарок", "Для детей"]


def seed(apps, schema_editor):
    OperationType = apps.get_model("catalog", "OperationType")
    MaterialCategory = apps.get_model("catalog", "MaterialCategory")
    Color = apps.get_model("catalog", "Color")
    Size = apps.get_model("core", "Size")
    Tag = apps.get_model("core", "Tag")
    SiteSettings = apps.get_model("core", "SiteSettings")

    SiteSettings.objects.get_or_create(pk=1)

    for order, (level, name, minutes, key) in enumerate(OPERATION_TYPES):
        OperationType.objects.create(
            level=level, name=name, name_key=name_key(name), default_minutes=minutes, key=key, order=order
        )

    for root_name, kind, children in MATERIAL_CATEGORIES:
        root = MaterialCategory.objects.create(
            name=root_name, name_key=name_key(root_name), kind=kind, path=root_name
        )
        for child in children:
            MaterialCategory.objects.create(
                name=child,
                name_key=name_key(child),
                kind=kind,
                parent=root,
                path=f"{root_name} → {child}",
            )

    for order, (name, code, hex_) in enumerate(COLORS):
        Color.objects.create(name=name, name_key=name_key(name), code=code, hex=hex_, order=order)

    for order, (name, code) in enumerate(SIZES):
        Size.objects.create(name=name, name_key=name_key(name), code=code, order=order)

    for name in TAGS:
        Tag.objects.create(name=name, name_key=name_key(name))


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
        ("core", "0001_initial"),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
