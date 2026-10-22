"""
Строки закупки: заполняем количество, сумму, поставщика и марку из старого предложения.
Рулонные материалы: единица расхода м² → пог. м, все количества делятся на ширину.
"""

from decimal import Decimal

from django.db import migrations

Q4 = Decimal("0.0001")


def fill_receipt_lines(apps, schema_editor):
    ReceiptLine = apps.get_model("stock", "ReceiptLine")
    for line in ReceiptLine.objects.select_related("offer"):
        packs = Decimal(line.packs or 0)
        line.quantity = (packs * (line.pack_size or 0)).quantize(Q4)
        line.total_price = (packs * (line.pack_price or 0)).quantize(Decimal("0.01"))
        if line.offer_id:
            line.supplier_id = line.offer.supplier_id
            line.manufacturer_id = line.offer.manufacturer_id
        line.save(update_fields=["quantity", "total_price", "supplier", "manufacturer"])


def rolls_in_meters(apps, schema_editor):
    Material = apps.get_model("catalog", "Material")
    models = {
        "offers": (apps.get_model("catalog", "MaterialOffer"), "pack_size"),
        "moves": (apps.get_model("stock", "StockMove"), "quantity"),
        "balances": (apps.get_model("stock", "StockBalance"), "quantity"),
        "packaging": (apps.get_model("sales", "ProductPackaging"), "quantity"),
        "components": (apps.get_model("production", "ModelComponent"), "quantity"),
        "parts": (apps.get_model("production", "Part"), "batch_material_qty"),
        "transfer": (apps.get_model("stock", "TransferLine"), "quantity"),
        "receiving": (apps.get_model("stock", "ReceivingLine"), "quantity"),
        "writeoff": (apps.get_model("stock", "WriteOffLine"), "quantity"),
    }
    History = apps.get_model("catalog", "MaterialPriceHistory")
    InventoryLine = apps.get_model("stock", "InventoryLine")
    ReceiptLine = apps.get_model("stock", "ReceiptLine")

    def swap_unit(obj, field):
        value = getattr(obj, field)
        setattr(obj, field, "" if value == "lm" else ("m2" if value == "" else value))

    for material in Material.objects.filter(form_type="roll"):
        material.unit = "lm"
        material.save(update_fields=["unit"])
        if not material.width_cm:
            continue
        width_m = material.width_cm / 100
        for model, field in models.values():
            for obj in model.objects.filter(material=material):
                setattr(obj, field, (getattr(obj, field) / width_m).quantize(Q4))
                fields = [field]
                if hasattr(obj, "entered_unit"):
                    swap_unit(obj, "entered_unit")
                    fields.append("entered_unit")
                if hasattr(obj, "batch_material_unit"):
                    swap_unit(obj, "batch_material_unit")
                    fields.append("batch_material_unit")
                obj.save(update_fields=fields)
        for row in History.objects.filter(material=material):
            row.pack_size = (row.pack_size / width_m).quantize(Q4)
            row.unit_price = row.unit_price * width_m
            row.save(update_fields=["pack_size", "unit_price"])
        for row in InventoryLine.objects.filter(material=material):
            row.counted_qty = (row.counted_qty / width_m).quantize(Q4)
            if row.difference is not None:
                row.difference = (row.difference / width_m).quantize(Q4)
            swap_unit(row, "entered_unit")
            row.save(update_fields=["counted_qty", "difference", "entered_unit"])
        for row in ReceiptLine.objects.filter(material=material):
            if row.pack_size:
                row.pack_size = (row.pack_size / width_m).quantize(Q4)
            row.quantity = (row.quantity / width_m).quantize(Q4)
            row.save(update_fields=["pack_size", "quantity"])


class Migration(migrations.Migration):
    dependencies = [
        ("stock", "0009_receipt_line_modes"),
        ("catalog", "0008_receipt_line_modes"),
        ("production", "0005_help_texts"),
        ("sales", "0004_help_texts"),
    ]
    operations = [
        migrations.RunPython(fill_receipt_lines, migrations.RunPython.noop),
        migrations.RunPython(rolls_in_meters, migrations.RunPython.noop),
    ]
