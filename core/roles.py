"""
Роли пользователей (ТЗ, раздел 2). Группы и права создаются автоматически
после каждой миграции, поэтому их состав всегда соответствует этому файлу.

Принципы:
- справочники (цвета, категории, метки, размеры, типы операций, оборудование)
  меняет только администратор, остальные выбирают из готового;
- основные записи никто, кроме администратора, не удаляет — только архив;
- себестоимость видят администратор и технолог (право sales.view_cost).
"""

from django.contrib.auth.models import Group, Permission

ADMIN = "Администратор"
TECHNOLOGIST = "Технолог"
SELLER = "Продавец"
OBSERVER = "Наблюдатель"

PROJECT_APPS = ("core", "catalog", "production", "sales", "gallery")

# Справочники, которые технолог только просматривает
REFERENCE_MODELS = [
    "core.sitesettings",
    "core.tag",
    "core.size",
    "catalog.color",
    "catalog.materialcategory",
    "catalog.equipment",
    "catalog.operationtype",
    "catalog.materialpricehistory",
    "production.productcategory",
]

# Основные записи: технолог создаёт и меняет, но не удаляет
WORK_MODELS = [
    "catalog.material",
    "production.part",
    "production.productmodel",
    "production.variant",
    "sales.product",
]

# Строки внутри основных записей: можно и удалять (убрать деталь из модели и т. п.)
LINE_MODELS = [
    "catalog.materialcolor",
    "production.partoperation",
    "production.modelpart",
    "production.modelcomponent",
    "production.modeloperation",
    "production.variantslot",
    "sales.productitem",
    "sales.productpackaging",
    "sales.productoperation",
    "gallery.photo",
]


def _perms(model_labels, actions):
    codenames = []
    for label in model_labels:
        app, model = label.split(".")
        for action in actions:
            codenames.append((app, f"{action}_{model}"))
    return codenames


def _resolve(pairs):
    perms = []
    for app, codename in pairs:
        perm = Permission.objects.filter(content_type__app_label=app, codename=codename).first()
        if perm:
            perms.append(perm)
    return perms


def sync_groups(**kwargs):
    admin, _ = Group.objects.get_or_create(name=ADMIN)
    admin.permissions.set(
        Permission.objects.filter(content_type__app_label__in=PROJECT_APPS + ("auth",))
    )

    technologist, _ = Group.objects.get_or_create(name=TECHNOLOGIST)
    technologist.permissions.set(
        _resolve(
            _perms(REFERENCE_MODELS, ["view"])
            + _perms(WORK_MODELS, ["view", "add", "change"])
            + _perms(LINE_MODELS, ["view", "add", "change", "delete"])
            + _perms(["sales.costsnapshot"], ["view", "add"])
            + [("sales", "view_cost")]
        )
    )

    seller, _ = Group.objects.get_or_create(name=SELLER)
    seller.permissions.set(_resolve(_perms(["sales.product", "gallery.photo"], ["view"])))

    observer, _ = Group.objects.get_or_create(name=OBSERVER)
    observer.permissions.set(
        _resolve(
            _perms(
                [
                    "sales.product",
                    "production.productmodel",
                    "production.variant",
                    "production.part",
                    "gallery.photo",
                ],
                ["view"],
            )
        )
    )
