"""
Дерево категорий изделий с кодами для артикулов — приложение 1 к «Правилам формирования
артикулов товаров» (коды интерьера начинаются с H вместо I; освещение — LT, LS;
ёлочные игрушки — NT как предложение). Существующие категории не трогаются.
"""

from django.db import migrations

from core.models import name_key

TREE = [
    # (уровень 1, уровень 2, уровень 3 или None, код)
    ("Детские товары", "Игрушки", "Развивающие игрушки", "TE"),
    ("Детские товары", "Игрушки", "Конструкторы", "TC"),
    ("Детские товары", "Игрушки", "Фигурки персонажей", "TF"),
    ("Детские товары", "Игрушки", "Куклы и аксессуары", "TD"),
    ("Детские товары", "Игрушки", "Роботы и трансформеры", "TR"),
    ("Детские товары", "Игрушки", "Игрушечное оружие и бластеры", "TG"),
    ("Детские товары", "Игрушки", "Игрушки-антистресс", "TAS"),
    ("Детские товары", "Игрушки", "Игрушки для купания", "TB"),
    ("Детские товары", "Игрушки", "Игрушечный транспорт", "TT"),
    ("Товары для дома и дачи", "Интерьер", "Крючки и крепления", "HFH"),
    ("Товары для дома и дачи", "Интерьер", "Интерьерные подставки и держатели", "HSH"),
    ("Товары для дома и дачи", "Интерьер", "Копилки", "HPB"),
    ("Товары для дома и дачи", "Интерьер", "Статуэтки и фигурки", "HM"),
    ("Товары для дома и дачи", "Интерьер", "Интерьерные наклейки", "HS"),
    ("Товары для дома и дачи", "Интерьер", "Коробочки для хранения, шкатулки", "HB"),
    ("Товары для дома и дачи", "Освещение", "Настольные светильники", "LT"),
    ("Товары для дома и дачи", "Освещение", "Бра", "LS"),
    ("Товары для дома и дачи", "Посуда и принадлежности", "Сервировка стола", "DTS"),
    ("Товары для дома и дачи", "Посуда и принадлежности", "Хранение продуктов", "DS"),
    ("Подарки, товары для праздников", "Сувенирная продукция", "Магниты сувенирные", "SM"),
    ("Подарки, товары для праздников", "Новогодние украшения", "Ёлочные игрушки", "NT"),
    ("Досуг", "Музыкальные инструменты", None, "MU"),  # без буквы I
    ("Спорт, туризм", "Товары для охоты и стрельбы", None, "HNT"),
    ("Спорт, туризм", "Товары для рыбалки", None, "FSH"),
    ("Спорт, туризм", "Спортивные товары", None, "GYM"),
]


def node(Category, parent, title, code=""):
    obj = Category.objects.filter(parent=parent, name_key=name_key(title)).first()
    path = f"{parent.path} → {title}" if parent else title
    if obj is None:
        obj = Category.objects.create(name=title, name_key=name_key(title), parent=parent, path=path, code=code)
    elif code and not obj.code and not Category.objects.filter(code=code).exists():
        obj.code = code
        obj.save(update_fields=["code"])
    return obj


def seed(apps, schema_editor):
    Category = apps.get_model("production", "ProductCategory")
    for level1, level2, level3, code in TREE:
        top = node(Category, None, level1)
        middle = node(Category, top, level2, "" if level3 else code)
        if level3:
            node(Category, middle, level3, code)


class Migration(migrations.Migration):
    dependencies = [("production", "0006_articles")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
