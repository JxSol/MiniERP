from django.core.management.base import BaseCommand, CommandError

from core.demo import load_demo
from costing.services import recalculate_all
from production.models import ProductModel


class Command(BaseCommand):
    help = "Загрузить примеры из ТЗ: светильник «Луна» и ёлочные шары с наборами."

    def handle(self, *args, **options):
        if ProductModel.objects.filter(name__in=["Светильник Луна", "Ёлочный шар"]).exists():
            raise CommandError("Демо-данные уже загружены.")
        load_demo()
        recalculate_all()
        self.stdout.write(self.style.SUCCESS("Демо-данные загружены."))
