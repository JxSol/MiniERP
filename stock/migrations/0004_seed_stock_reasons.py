"""Начальные причины прихода и списания (ТЗ, приложение А)."""

from django.db import migrations

from core.models import name_key

REASONS = [
    # (где используется, название, порядок)
    ("in", "Начальные остатки", 10),
    ("in", "Покупка готовых деталей или изделий", 20),
    ("in", "Возврат от покупателя", 30),
    ("in", "Излишки", 40),
    ("out", "Брак, обнаруженный на складе", 10),
    ("out", "Образцы и подарки", 20),
    ("out", "Порча или утеря", 30),
    ("out", "Личное использование", 40),
    ("both", "Прочее", 90),
]


def seed(apps, schema_editor):
    StockReason = apps.get_model("stock", "StockReason")
    for direction, name, order in REASONS:
        StockReason.objects.get_or_create(
            name_key=name_key(name),
            defaults={"name": name, "direction": direction, "order": order},
        )


class Migration(migrations.Migration):
    dependencies = [("stock", "0003_receiving_writeoff")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
