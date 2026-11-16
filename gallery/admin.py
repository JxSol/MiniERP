"""Вкладка «Фото» для деталей, моделей, вариантов и товаров."""

from unfold.admin import TabularInline
from unfold.decorators import display

from core.admin_utils import thumbnail

from .models import MAX_UPLOAD_MB, PHOTO_EXTENSIONS, Photo, PhotoKind


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
    # Больше строк в таблицу не добавить. Фото теперь можно бросить в карточку пачкой, а за одно
    # сохранение Django принимает не больше 100 файлов (DATA_UPLOAD_MAX_NUMBER_FILES)
    max_num = 50

    # Над таблицей — область, куда фото перетаскивают, вставляют из буфера обмена или
    # выбирают сразу несколько. Скрипт на каждый файл добавляет обычную строку этой таблицы,
    # поэтому проверки и сохранение остаются прежними: вместе с карточкой
    template_before = "gallery/photo_drop.html"
    max_upload_mb = MAX_UPLOAD_MB
    photo_extensions = ",".join(PHOTO_EXTENSIONS)

    class Media:
        css = {"all": ("gallery/photo_drop.css",)}
        js = ("gallery/photo_drop.js",)

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
