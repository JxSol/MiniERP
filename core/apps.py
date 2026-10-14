from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Общее"

    def ready(self):
        from django.db.models.signals import post_migrate

        from .roles import sync_groups

        # Без sender: права всех приложений к этому моменту уже созданы
        post_migrate.connect(sync_groups, dispatch_uid="core-sync-groups")
