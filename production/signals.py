from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from catalog.models import Material

from .models import ModelPart, Part, PartColoring, PartMaterial, Variant, VariantSlot
from .services import ensure_variant_colorings, sync_model_slots, sync_variant_slots


def _sync_models_of(part: Part) -> None:
    """Сменились позиции или группы детали — группы вариантов моделей с ней должны совпасть."""
    for line in part.model_lines.select_related("model"):
        sync_model_slots(line.model)


@receiver(post_save, sender=Part, dispatch_uid="part-material-slots")
def part_saved(sender, instance, created, **kwargs):
    if not created:
        _sync_models_of(instance)


@receiver([post_save, post_delete], sender=PartMaterial, dispatch_uid="part-position-slots")
def part_position_changed(sender, instance, **kwargs):
    try:
        part = instance.part
    except Part.DoesNotExist:  # деталь удаляется целиком
        return
    _sync_models_of(part)
    for coloring in part.colorings.all():
        coloring.refresh_label()


@receiver(post_save, sender=Variant, dispatch_uid="variant-slots-on-create")
def variant_created(sender, instance, created, **kwargs):
    if created:
        sync_variant_slots(instance)


@receiver(post_save, sender=VariantSlot, dispatch_uid="variant-slot-colorings")
def variant_slot_saved(sender, instance, **kwargs):
    ensure_variant_colorings(instance.variant)


@receiver([post_save, post_delete], sender=ModelPart, dispatch_uid="model-part-slots")
def model_part_changed(sender, instance, **kwargs):
    try:
        model = instance.model
    except Exception:  # модель удаляется целиком
        return
    sync_model_slots(model)


@receiver(post_save, sender=Material, dispatch_uid="material-coloring-labels")
def material_renamed(sender, instance, created, **kwargs):
    if created:
        return
    for coloring in PartColoring.objects.filter(items__material=instance).distinct():
        coloring.refresh_label()
