"""Вкладка «Фото» для деталей, моделей, вариантов и товаров."""

from unfold.admin import TabularInline
from unfold.decorators import display

from core.admin_utils import thumbnail

from .models import Photo, PhotoKind


class PhotoInline(TabularInline):
    model = Photo
    extra = 0
    tab = True
    fields = ("preview", "image", "kind", "caption", "order")
    readonly_fields = ("preview",)
    ordering_field = "order"
    hide_ordering_field = True
    verbose_name_plural = "Фото"
    allowed_kinds: tuple[str, ...] = (PhotoKind.MAIN, PhotoKind.EXTRA)

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if db_field.name == "kind":
            kwargs["choices"] = [c for c in PhotoKind.choices if c[0] in self.allowed_kinds]
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    @display(description="")
    def preview(self, obj):
        return thumbnail(obj.preview_url, 64) if obj and obj.pk else "—"


class PartPhotoInline(PhotoInline):
    fk_name = "part"
    allowed_kinds = (PhotoKind.TECH, PhotoKind.EXTRA)


class ModelPhotoInline(PhotoInline):
    fk_name = "model"
    allowed_kinds = (PhotoKind.MAIN, PhotoKind.TECH, PhotoKind.EXTRA)


class VariantPhotoInline(PhotoInline):
    fk_name = "variant"


class ProductPhotoInline(PhotoInline):
    fk_name = "product"


class PhotoUploaderMixin:
    """Записывает, кто загрузил фото."""

    def save_formset(self, request, form, formset, change):
        if formset.model is Photo:
            for photo in formset.save(commit=False):
                if not photo.pk:
                    photo.uploaded_by = request.user
                photo.save()
            for photo in formset.deleted_objects:
                photo.delete()
            formset.save_m2m()
            return
        super().save_formset(request, form, formset, change)
