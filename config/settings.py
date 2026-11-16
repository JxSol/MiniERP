"""
Настройки Django для мини-ERP.

Все значения, которые отличаются между компьютером разработчика и сервером,
читаются из переменных окружения (файл .env, образец — .env.example).
"""

from pathlib import Path

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CSRF_TRUSTED_ORIGINS=(list, []),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

if not DEBUG:
    if SECRET_KEY in ("", "change-me") or len(SECRET_KEY) < 40:
        raise ImproperlyConfigured(
            "Задайте надёжный SECRET_KEY в .env (см. README, раздел «Настройки»)."
        )
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


# Приложения

INSTALLED_APPS = [
    # unfold должен идти раньше django.contrib.admin
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.import_export",
    "unfold.contrib.simple_history",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "import_export",
    "simple_history",
    # Приложения проекта
    "core",
    "catalog",
    "production",
    "sales",
    "gallery",
    "costing",
    "stock",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
]
if not DEBUG:
    # На сервере статику (оформление админки, скрипты) отдаёт само приложение через WhiteNoise:
    # сжатой и с заголовками кеширования. При DEBUG=True это делает runserver
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# База данных: PostgreSQL (в Docker, см. docker-compose.yml)

DATABASES = {"default": env.db("DATABASE_URL")}


# Пароли

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Язык и время

LANGUAGE_CODE = "ru"
TIME_ZONE = "Asia/Almaty"
USE_I18N = True
USE_TZ = True
LOCALE_PATHS = [BASE_DIR / "locale"]
FORMS_URLFIELD_ASSUME_HTTPS = True


# Статика и загруженные файлы (фото изделий)

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Фото и файлы моделей отдаёт core.media: только вошедшим сотрудникам
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedStaticFilesStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Журнал. На сервере ошибки пишутся в вывод контейнера (docker compose logs web): без этого
# Django при DEBUG=False только пытается отправить их письмом администраторам

if not DEBUG:
    LOGGING = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
        },
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "plain"},
        },
        "root": {"handlers": ["console"], "level": "WARNING"},
        "loggers": {
            "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        },
    }


# Оформление админки

from django.templatetags.static import static  # noqa: E402
from django.urls import reverse, reverse_lazy  # noqa: E402


def _item(title, icon, url_name, perm):
    return {
        "title": title,
        "icon": icon,
        "link": reverse_lazy(url_name),
        "permission": f"core.navigation.{perm}",
    }


def _tab(title, url_name, perm):
    return {"title": title, "link": reverse_lazy(url_name), "permission": f"core.navigation.{perm}"}


def _balance_tab(title, kind):
    """Вкладка остатков: тот же список с параметром ?kind=; без параметра — материалы."""
    return {
        "title": title,
        "link": lambda request: f"{reverse('admin:stock_stockbalance_changelist')}?kind={kind}",
        "active": lambda request: request.GET.get("kind", "material") == kind,
        "permission": "core.navigation.view_stock",
    }


UNFOLD = {
    "SITE_TITLE": "Мини-ERP",
    "SITE_HEADER": "Мини-ERP",
    "SITE_SYMBOL": "precision_manufacturing",
    "SHOW_HISTORY": True,
    "STYLES": [lambda request: static("core/admin.css")],
    # Удаление новой строки в таблицах карточек без потери введённого в строках ниже
    "SCRIPTS": [lambda request: static("core/inline_delete.js")],
    # Глобальный поиск по артикулу, названию, штрихкоду (Ctrl+K)
    "COMMAND": {"search_models": True, "show_history": True},
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": False,
        "navigation": [
            {
                "title": "Товары",
                "items": [
                    _item("Товары", "sell", "admin:sales_product_changelist", "view_product"),
                ],
            },
            {
                "title": "Склад",
                "items": [
                    _item(
                        "Остатки",
                        "inventory_2",
                        "admin:stock_stockbalance_changelist",
                        "view_stock",
                    ),
                    _item(
                        "Закупки", "shopping_cart", "admin:stock_receipt_changelist", "view_receipt"
                    ),
                    _item(
                        "Приход и списание",
                        "swap_vert",
                        "admin:stock_receiving_changelist",
                        "view_receiving",
                    ),
                    _item(
                        "Перемещения",
                        "local_shipping",
                        "admin:stock_transfer_changelist",
                        "view_transfer",
                    ),
                    _item(
                        "Инвентаризация",
                        "fact_check",
                        "admin:stock_inventory_changelist",
                        "view_inventory",
                    ),
                    _item(
                        "Движения по складу",
                        "list_alt",
                        "admin:stock_stockmove_changelist",
                        "view_moves",
                    ),
                ],
            },
            {
                "title": "Производство",
                "items": [
                    _item(
                        "Журнал производства",
                        "conveyor_belt",
                        "admin:stock_productionrun_changelist",
                        "view_run",
                    ),
                    _item(
                        "Модели",
                        "deployed_code",
                        "admin:production_productmodel_changelist",
                        "view_model",
                    ),
                    _item(
                        "Варианты", "palette", "admin:production_variant_changelist", "view_variant"
                    ),
                    _item("Детали", "extension", "admin:production_part_changelist", "view_part"),
                ],
            },
            {
                "title": "Справочники",
                "collapsible": True,
                "items": [
                    _item(
                        "Материалы",
                        "inventory",
                        "admin:catalog_material_changelist",
                        "view_material",
                    ),
                    _item(
                        "Палитра цветов",
                        "format_paint",
                        "admin:catalog_color_changelist",
                        "view_color",
                    ),
                    _item(
                        "Производители",
                        "factory",
                        "admin:catalog_manufacturer_changelist",
                        "view_manufacturer",
                    ),
                    _item(
                        "Поставщики",
                        "storefront",
                        "admin:catalog_supplier_changelist",
                        "view_supplier",
                    ),
                    _item(
                        "Оборудование",
                        "print",
                        "admin:catalog_equipment_changelist",
                        "view_equipment",
                    ),
                    _item(
                        "Типы операций",
                        "handyman",
                        "admin:catalog_operationtype_changelist",
                        "view_operationtype",
                    ),
                    _item(
                        "Категории материалов",
                        "category",
                        "admin:catalog_materialcategory_changelist",
                        "view_materialcategory",
                    ),
                    _item(
                        "Категории изделий",
                        "account_tree",
                        "admin:production_productcategory_changelist",
                        "view_productcategory",
                    ),
                    _item(
                        "Склады", "warehouse", "admin:stock_warehouse_changelist", "view_warehouse"
                    ),
                    _item(
                        "Причины прихода и списания",
                        "rule",
                        "admin:stock_stockreason_changelist",
                        "view_stockreason",
                    ),
                    _item("Метки", "label", "admin:core_tag_changelist", "view_tag"),
                    _item("Размеры", "straighten", "admin:core_size_changelist", "view_size"),
                ],
            },
            {
                "title": "Система",
                "collapsible": True,
                "items": [
                    _item(
                        "Общие настройки",
                        "settings",
                        "admin:core_sitesettings_changelist",
                        "view_settings",
                    ),
                    _item("Пользователи", "person", "admin:auth_user_changelist", "view_user"),
                    _item("Роли", "group", "admin:auth_group_changelist", "view_group"),
                ],
            },
        ],
    },
    # Вкладки над списками: остатки по уровням и три шага журнала производства
    "TABS": [
        {
            "models": ["stock.stockbalance"],
            "items": [
                _balance_tab("Материалы", "material"),
                _balance_tab("Детали", "part"),
                _balance_tab("Изделия", "variant"),
                _balance_tab("Товары", "product"),
            ],
        },
        {
            "models": ["stock.productionrun", "stock.assembly", "stock.packing"],
            "items": [
                _tab("Выпуск деталей", "admin:stock_productionrun_changelist", "view_run"),
                _tab("Сборка изделий", "admin:stock_assembly_changelist", "view_assembly"),
                _tab("Упаковка товаров", "admin:stock_packing_changelist", "view_packing"),
            ],
        },
        {
            "models": ["stock.receiving", "stock.writeoff"],
            "items": [
                _tab("Оприходование", "admin:stock_receiving_changelist", "view_receiving"),
                _tab("Списание", "admin:stock_writeoff_changelist", "view_writeoff"),
            ],
        },
    ],
}
