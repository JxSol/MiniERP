"""
Фотографии деталей, моделей, вариантов и товаров (ТЗ п. 6.9).

Правила:
- у модели, варианта и товара всегда ровно одно главное фото (если фото есть):
  первое загруженное становится главным, новое главное понижает прежнее,
  при удалении главного главным становится следующее;
- техническое фото — одно, только у модели или детали;
- фото с телефона поворачиваются по EXIF, HEIC переводится в JPEG,
  для списков создаётся миниатюра.
"""

import io
from pathlib import PurePath

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_delete
from django.dispatch import receiver
from PIL import Image, ImageOps

MAX_UPLOAD_MB = 15
THUMB_SIZE = (480, 480)
WEB_FORMATS = {"JPEG", "PNG", "WEBP"}


class PhotoKind(models.TextChoices):
    MAIN = "main", "Главное"
    TECH = "tech", "Техническое"
    EXTRA = "extra", "Дополнительное"


def validate_size(file):
    if file.size > MAX_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f"Файл больше {MAX_UPLOAD_MB} МБ.")


def photo_path(photo: "Photo", filename: str) -> str:
    name = PurePath(filename).name
    if photo.part_id:
        return f"parts/{photo.part.code}/{name}"
    if photo.model_id:
        return f"models/{photo.model.code}/{name}"
    if photo.variant_id:
        return f"models/{photo.variant.model.code}/{photo.variant.code}/{name}"
    return f"products/{photo.product.sku}/{name}"


def thumb_path(photo: "Photo", filename: str) -> str:
    return "thumbs/" + photo_path(photo, filename)


OWNER_FIELDS = ("part", "model", "variant", "product")


def _only(field: str) -> Q:
    return Q(**{f"{f}__isnull": f != field for f in OWNER_FIELDS})


class Photo(models.Model):
    part = models.ForeignKey(
        "production.Part", null=True, blank=True, on_delete=models.CASCADE, related_name="photos"
    )
    model = models.ForeignKey(
        "production.ProductModel",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="photos",
    )
    variant = models.ForeignKey(
        "production.Variant", null=True, blank=True, on_delete=models.CASCADE, related_name="photos"
    )
    product = models.ForeignKey(
        "sales.Product", null=True, blank=True, on_delete=models.CASCADE, related_name="photos"
    )

    image = models.ImageField(
        "Файл",
        upload_to=photo_path,
        validators=[
            FileExtensionValidator(["jpg", "jpeg", "png", "webp", "heic", "heif"]),
            validate_size,
        ],
    )
    thumbnail = models.ImageField(upload_to=thumb_path, blank=True, editable=False)
    kind = models.CharField(
        "Тип", max_length=10, choices=PhotoKind.choices, default=PhotoKind.EXTRA
    )
    caption = models.CharField("Подпись", max_length=150, blank=True)
    order = models.PositiveIntegerField("Порядок", default=0)
    uploaded_at = models.DateTimeField("Загружено", auto_now_add=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, editable=False
    )

    class Meta:
        verbose_name = "Фото"
        verbose_name_plural = "Фото"
        ordering = ["order", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=_only("part") | _only("model") | _only("variant") | _only("product"),
                name="gallery_photo_single_owner",
            )
        ]

    def __str__(self):
        return f"{self.get_kind_display()} фото"

    # --- Владелец ---

    def owner_filter(self) -> dict:
        for field in OWNER_FIELDS:
            value = getattr(self, f"{field}_id")
            if value:
                return {f"{field}_id": value}
        return {}

    def siblings(self):
        return Photo.objects.filter(**self.owner_filter()).exclude(pk=self.pk)

    # --- Правила ---

    def clean(self):
        super().clean()
        if self.kind == PhotoKind.TECH and (self.variant_id or self.product_id):
            raise ValidationError({"kind": "Техническое фото бывает только у модели или детали."})
        if self.kind == PhotoKind.MAIN and self.part_id:
            raise ValidationError(
                {"kind": "У детали нет главного фото — только техническое и дополнительные."}
            )

    def save(self, *args, **kwargs):
        if self.part_id and self.kind == PhotoKind.MAIN:
            self.kind = PhotoKind.EXTRA
        if (
            not self.part_id
            and self.kind == PhotoKind.EXTRA
            and not self.siblings().filter(kind=PhotoKind.MAIN).exists()
        ):
            self.kind = PhotoKind.MAIN

        if self.image and not getattr(self.image, "_committed", True):
            self._prepare_image()

        super().save(*args, **kwargs)

        if self.kind in (PhotoKind.MAIN, PhotoKind.TECH):
            self.siblings().filter(kind=self.kind).update(kind=PhotoKind.EXTRA)

    def _prepare_image(self):
        """Поворот по EXIF, HEIC → JPEG, миниатюра."""
        upload = self.image.file
        upload.seek(0)
        img = Image.open(upload)
        original_format = img.format
        img = ImageOps.exif_transpose(img)
        stem = PurePath(self.image.name).stem

        if original_format not in WEB_FORMATS:
            buffer = io.BytesIO()
            img.convert("RGB").save(buffer, "JPEG", quality=92)
            self.image.save(f"{stem}.jpg", ContentFile(buffer.getvalue()), save=False)

        thumb = img.copy()
        thumb.thumbnail(THUMB_SIZE)
        buffer = io.BytesIO()
        thumb.convert("RGB").save(buffer, "JPEG", quality=85)
        self.thumbnail.save(f"{stem}.jpg", ContentFile(buffer.getvalue()), save=False)

    @property
    def preview_url(self) -> str:
        if self.thumbnail:
            return self.thumbnail.url
        return self.image.url if self.image else ""


@receiver(post_delete, sender=Photo)
def promote_next_main(sender, instance: Photo, **kwargs):
    """Удалили главное фото — главным становится следующее."""
    if instance.kind != PhotoKind.MAIN:
        return
    owner = instance.owner_filter()
    if not owner or Photo.objects.filter(**owner, kind=PhotoKind.MAIN).exists():
        return
    nxt = Photo.objects.filter(**owner, kind=PhotoKind.EXTRA).order_by("order", "pk").first()
    if nxt:
        Photo.objects.filter(pk=nxt.pk).update(kind=PhotoKind.MAIN)


def main_photo(**owner) -> Photo | None:
    return Photo.objects.filter(**owner, kind=PhotoKind.MAIN).first()


def main_photo_for_variant(variant) -> Photo | None:
    """Главное фото варианта с наследованием: вариант → модель."""
    return main_photo(variant=variant) or main_photo(model=variant.model)


def main_photo_for_model(model) -> Photo | None:
    """Фото для карточки модели: главное фото первого варианта, иначе фото самой модели."""
    first = model.variants.order_by("code").first()
    if first is not None:
        photo = main_photo(variant=first)
        if photo:
            return photo
    return main_photo(model=model)


def main_photo_for_product(product) -> Photo | None:
    """Главное фото товара с наследованием: товар → вариант → модель (ТЗ п. 5.2)."""
    photo = main_photo(product=product)
    if photo:
        return photo
    variant = product.main_variant()
    return main_photo_for_variant(variant) if variant is not None else None
