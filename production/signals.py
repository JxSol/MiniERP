from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import ModelPart, Variant
from .services import sync_model_slots, sync_variant_slots


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
