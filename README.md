# Мини-ERP для производства

Учёт изделий (3D-печать и лазер), их себестоимости и цен на площадках.
Техническое задание: [TZ.md](TZ.md).

## Стек

Python 3.13, Django 5.2, PostgreSQL 16 (в Docker), админка на django-unfold.

## Запуск для разработки (Windows)

Нужны Python 3.13 и Docker Desktop.

```powershell
# 1. База данных
docker compose up -d

# 2. Окружение Python
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt

# 3. Настройки
copy .env.example .env
# и задать SECRET_KEY, сгенерировать можно так:
.venv\Scripts\python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"

# 4. Таблицы, начальные справочники и роли
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py createsuperuser

# 5. (необязательно) примеры из ТЗ: светильник «Луна» и ёлочные шары
.venv\Scripts\python manage.py load_demo

# 6. Запуск
.venv\Scripts\python manage.py runserver
```

Админка: http://127.0.0.1:8000/admin/

## Роли

Группы создаются автоматически при `migrate` (см. [core/roles.py](core/roles.py)).
Пользователя добавляют в группу на странице «Система → Пользователи».

| Группа | Что может |
|---|---|
| Администратор | Всё, включая справочники и настройки |
| Технолог | Материалы, детали, модели, варианты, товары; видит себестоимость |
| Продавец | Только просмотр товаров, без себестоимости |
| Наблюдатель | Просмотр каталога без себестоимости |

## Как устроен код

| Приложение | Что внутри |
|---|---|
| `core` | Общие настройки, метки, размеры, роли, базовые классы, демо-данные |
| `catalog` | Материалы и цвета, оборудование, типы операций, категории материалов |
| `production` | Детали, модели, варианты, генераторы (варианты, наборы, размеры) |
| `sales` | Товары (поштучные и наборы), упаковка, снимки себестоимости |
| `gallery` | Фото с правилами «главное / техническое / дополнительные» |
| `costing` | Движок себестоимости (`engine.py`, чистые функции) и пересчёт |
| `stock` | Склады, остатки, движения и записи: купили, напечатали, собрали, упаковали, переместили, пересчитали |

Себестоимость пересчитывается автоматически после любого изменения.
Вручную: `python manage.py recalc_costs`.

## Проверки

Нужен запущенный Postgres (`docker compose up -d`): тесты создают базу `test_erp`.

```powershell
.venv\Scripts\ruff check .
.venv\Scripts\ruff format --check .
.venv\Scripts\python -m pytest
```

Пример «Луны» из ТЗ (п. 4.9) — обязательный тест: итог должен совпадать до копейки.

## Перевод интерфейса

Подписи unfold, которых нет в его пакете, переведены в `locale/ru/LC_MESSAGES/django.po`.
После правок: `python scripts/compile_locale.py` (собранный `django.mo` коммитится вместе с `.po`).
