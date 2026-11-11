"""Готовый цвет палитры «Радуга»: кружок-образец у него — плавный спектр."""

from django.db import migrations
from django.db.models import Max

from core.models import name_key

RAINBOW = "Радуга"


def add_rainbow(apps, schema_editor):
    Color = apps.get_model("catalog", "Color")
    if Color.objects.filter(kind="rainbow").exists():
        return
    # Цвет с таким названием уже завели сами — «Радугой» становится он
    existing = Color.objects.filter(name_key=name_key(RAINBOW)).first()
    if existing:
        existing.kind = "rainbow"
        existing.save(update_fields=["kind"])
        return
    last = Color.objects.aggregate(last=Max("order"))["last"]
    Color.objects.create(
        name=RAINBOW,
        name_key=name_key(RAINBOW),
        kind="rainbow",
        order=0 if last is None else last + 1,
    )


class Migration(migrations.Migration):
    dependencies = [("catalog", "0011_color_mix")]

    operations = [migrations.RunPython(add_rainbow, migrations.RunPython.noop)]
