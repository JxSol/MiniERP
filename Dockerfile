# Образ мини-ERP для сервера: Django под waitress. База данных — отдельный контейнер,
# см. docker-compose.prod.yml и README, раздел «Запуск на сервере».
FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Зависимости — отдельным слоем: он пересобирается, только когда меняется requirements.txt
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Статика собирается при сборке образа. Настоящие секреты для этого не нужны: ключ ниже
# нигде не используется, а к базе сборка не обращается
RUN SECRET_KEY=only-for-collectstatic-at-image-build-time-0000 DEBUG=False \
    DATABASE_URL=postgres://build:build@localhost/build \
    python manage.py collectstatic --noinput

# Приложение работает не от root; на запись ему нужна только папка с фото
RUN useradd --uid 10001 --no-create-home app \
    && mkdir -p /app/media \
    && chown app /app/media
USER app

EXPOSE 8000

# Перед запуском применяются миграции. Заголовкам X-Forwarded-* здесь можно доверять: порт
# наружу не публикуется, запросы приходят только от обратного прокси по сети Docker
CMD ["sh", "-c", "python manage.py migrate --noinput && exec waitress-serve --listen=0.0.0.0:8000 --threads=6 --trusted-proxy='*' --trusted-proxy-headers='x-forwarded-for x-forwarded-proto' config.wsgi:application"]
