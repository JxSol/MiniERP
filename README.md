# Мини-ERP для производства

Учёт изделий (3D-печать и лазер), их себестоимости и цен на площадках Kaspi, Wildberries и Ozon.
Техническое задание: [TZ.md](TZ.md).

## Стек

Python 3.13, Django 5.2, PostgreSQL 16 (в Docker), админка на django-unfold.

## Запуск для разработки (Windows)

Нужны Python 3.13 и Docker Desktop.

```bash
# 1. База данных
docker compose up -d

# 2. Окружение Python
py -3.13 -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 3. Настройки
copy .env.example .env      # и задать SECRET_KEY

# 4. Миграции и администратор
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py createsuperuser

# 5. Запуск
.venv\Scripts\python manage.py runserver
```

Админка: http://127.0.0.1:8000/admin/
