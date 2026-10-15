from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import ModelPart, Part, Variant
from .services import sync_model_slots, sync_variant_slots


@receiver(post_save, sender=Part, dispatch_uid="part-material-slots")
def part_saved(sender, instance, created, **kwargs):
    """Сменили материал детали — слоты вариантов должны остаться совместимыми."""
    if created:
        return
    for line in instance.model_lines.select_related("model"):
        if line.slot:
            sync_model_slots(line.model)


@receiver(post_save, sender=Variant, dispatch_uid="variant-slots-on-create")
def variant_created(sender, instance, created, **kwargs):
    if created:
        sync_variant_slots(instance)


@receiver([post_save, post_delete], sender=ModelPart, dispatch_uid="model-part-slots")
def model_part_changed(sender, instance, **kwargs):
    try:
        model = instance.model
    except Exception:  # модель удаляется целиком
        return
    sync_model_slots(model)
