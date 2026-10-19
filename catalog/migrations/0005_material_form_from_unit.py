"""Forma ucheta dlya sushchestvuyushchikh materialov: list -> listovoy, m2 -> rulonnyy, m -> pogonnyy."""

from django.db import migrations


def forward(apps, schema_editor):
    Material = apps.get_model("catalog", "Material")
    for unit, form_type in (("sheet", "sheet"), ("m2", "roll"), ("m", "length")):
        Material.objects.filter(unit=unit).update(form_type=form_type)


class Migration(migrations.Migration):
    dependencies = [("catalog", "0004_material_forms")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
