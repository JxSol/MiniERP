from django.core.management.base import BaseCommand

from costing.services import recalculate_all


class Command(BaseCommand):
    help = "Пересчитать себестоимость всех деталей, моделей, вариантов и товаров."

    def handle(self, *args, **options):
        recalculate_all()
        self.stdout.write(self.style.SUCCESS("Себестоимость пересчитана."))
