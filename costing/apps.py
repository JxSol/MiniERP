from django.apps import AppConfig


class CostingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "costing"
    verbose_name = "Себестоимость"

    def ready(self):
        from . import signals

        signals.connect()
