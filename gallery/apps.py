from django.apps import AppConfig


class GalleryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "gallery"
    verbose_name = "Фото"

    def ready(self):
        # Фото с iPhone (HEIC) открываются так же, как JPEG
        from pillow_heif import register_heif_opener

        register_heif_opener()
