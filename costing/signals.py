"""
Автоматический пересчёт себестоимости после изменений (ТЗ п. 10.2).

Пересчёт ставится в очередь на момент фиксации транзакции и выполняется один
раз, сколько бы записей ни сохранилось в рамках одного сохранения формы.
"""

from django.db import transaction
from django.db.models.signals import post_delete, post_save

from catalog.models import Equipment, Material, OperationType
from core.models import SiteSettings
from production.models import (
    ModelComponent,
    ModelOperation,
    ModelPart,
    Part,
    PartOperation,
    ProductModel,
    Variant,
    VariantSlot,
)
from sales.models import Product, ProductItem, ProductOperation, ProductPackaging

from .services import recalculate_all

TRIGGERS = (
    SiteSettings,
    Material,
    Equipment,
    OperationType,
    Part,
    PartOperation,
    ProductModel,
    ModelPart,
    ModelComponent,
    ModelOperation,
    Variant,
    VariantSlot,
    Product,
    ProductItem,
    ProductPackaging,
    ProductOperation,
)


def _run():
    recalculate_all()


def schedule_recalculation(**kwargs):
    connection = transaction.get_connection()
    if connection.in_atomic_block and any(item[1] is _run for item in connection.run_on_commit):
        return
    transaction.on_commit(_run)


def connect():
    for model in TRIGGERS:
        post_save.connect(
            schedule_recalculation, sender=model, dispatch_uid=f"recalc-save-{model.__name__}"
        )
        post_delete.connect(
            schedule_recalculation, sender=model, dispatch_uid=f"recalc-del-{model.__name__}"
        )
