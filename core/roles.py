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

# Кто за что отвечает — показывается в списке ролей, в карточке роли и под полем «Роли»
ROLE_DESCRIPTIONS = {
    ADMIN: "Владелец системы. Отвечает за справочники (цвета, категории, оборудование, типы "
    "операций, поставщики, склады, причины), общие настройки, пользователей и роли. "
    "Может всё, в том числе отменять проведённые документы и удалять черновики. "
    "Видит себестоимость.",
    TECHNOLOGIST: "Отвечает за производство и склад: заводит материалы с предложениями "
    "поставщиков, детали, модели, варианты и товары; ведёт все документы склада — закупки, "
    "выпуск деталей, сборку, упаковку, перемещения, инвентаризацию, оприходование и списание. "
    "Видит и фиксирует себестоимость. Справочники администратора не меняет, записи "
    "не удаляет — только в архив.",
    SELLER: "Работает с товарами для продажи: видит карточки товаров, артикулы и фото; "
    "на этапе продаж будет проводить продажи и скидки в пределах своего лимита. "
    "Не видит себестоимость, прибыль, маржу и цены закупки, не заходит в склад "
    "и производство.",
    OBSERVER: "Только смотрит каталог: товары, модели, варианты, детали и фото. Ничего "
    "не меняет и не видит себестоимость. Подходит бухгалтеру, партнёру или стажёру.",
}


def role_description(name: str) -> str:
    return ROLE_DESCRIPTIONS.get(name, "")


PROJECT_APPS = ("core", "catalog", "production", "sales", "gallery", "stock")

# Справочники, которые технолог только просматривает
REFERENCE_MODELS = [
    "core.sitesettings",
    "core.tag",
    "core.size",
    "catalog.color",
    "catalog.manufacturer",
    "catalog.supplier",
    "catalog.materialcategory",
    "catalog.equipment",
    "catalog.operationtype",
    "catalog.materialpricehistory",
    "production.productcategory",
    "production.partcoloring",
    "stock.warehouse",
    "stock.stockreason",
    "stock.stockbalance",
    "stock.stockmove",
]

# Основные записи: технолог создаёт и меняет, но не удаляет
WORK_MODELS = [
    "catalog.material",
    "production.part",
    "production.productmodel",
    "production.variant",
    "sales.product",
    "stock.receipt",
    "stock.productionrun",
    "stock.assembly",
    "stock.packing",
    "stock.transfer",
    "stock.inventory",
    "stock.receiving",
    "stock.writeoff",
]

# Строки внутри основных записей: можно и удалять (убрать деталь из модели и т. п.)
LINE_MODELS = [
    "catalog.materialoffer",
    "production.partmaterial",
    "production.partoperation",
    "production.modelpart",
    "production.modelcomponent",
    "production.modeloperation",
    "production.variantslot",
    "sales.productitem",
    "sales.productpackaging",
    "sales.productoperation",
    "gallery.photo",
    "stock.receiptline",
    "stock.productionrunmaterial",
    "stock.transferline",
    "stock.inventoryline",
    "stock.receivingline",
    "stock.writeoffline",
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
                    "production.partmaterial",
                    "production.partcoloring",
                    "gallery.photo",
                ],
                ["view"],
            )
        )
    )
