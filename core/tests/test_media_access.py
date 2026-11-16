"""
Загруженные файлы отдаются только вошедшим сотрудникам (core/media.py), а всё, что относится
к деталям, — только тем, у кого есть право на раздел «Детали». Заодно проверяется, что
настройки для сервера (DEBUG=False) вообще загружаются: обычные тесты идут с DEBUG=True.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.contrib.auth.models import Group, User

from core.roles import SELLER, TECHNOLOGIST

pytestmark = pytest.mark.django_db


def put(settings, relative: str, content: bytes = b"file-content") -> str:
    """Кладёт файл в папку загруженных файлов и возвращает его адрес."""
    path = Path(settings.MEDIA_ROOT) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return f"/media/{relative}"


def user_in(group: str, username: str) -> User:
    user = User.objects.create_user(username, password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    return user


def test_anonymous_is_sent_to_login(client, settings):
    url = put(settings, "models/M-0000001/photo.png")
    response = client.get(url)
    assert response.status_code == 302
    assert response["Location"].startswith("/admin/login/")
    assert "next=/media/models/M-0000001/photo.png" in response["Location"]


def test_logged_in_user_without_staff_flag_gets_nothing(client, settings):
    url = put(settings, "models/M-0000001/photo.png")
    client.force_login(User.objects.create_user("visitor", password="x"))
    assert client.get(url).status_code == 302


def test_staff_gets_the_file(client, admin_user, settings):
    url = put(settings, "models/M-0000001/photo.png", b"png-bytes")
    client.force_login(admin_user)
    response = client.get(url)
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"png-bytes"
    # браузеру можно хранить файл у себя, но не общим прокси-серверам
    assert "private" in response["Cache-Control"] and "max-age=86400" in response["Cache-Control"]
    assert client.get("/media/models/M-0000001/missing.png").status_code == 404


def test_part_files_need_access_to_parts(client, settings):
    """Продавец видит фото товаров и моделей, но не файлы и фото деталей."""
    product_photo = put(settings, "products/SKU-0000001/photo.jpg")
    model_photo = put(settings, "thumbs/models/M-0000001/photo.jpg")
    part_urls = [
        put(settings, "part_files/plafon.3mf"),
        put(settings, "parts/C-FDM-0000001/layout.png"),
        put(settings, "thumbs/parts/C-FDM-0000001/layout.jpg"),
    ]

    client.force_login(user_in(SELLER, "seller"))
    assert client.get(product_photo).status_code == 200
    assert client.get(model_photo).status_code == 200
    for url in part_urls:
        assert client.get(url).status_code == 403, url

    client.force_login(user_in(TECHNOLOGIST, "technologist"))
    for url in part_urls:
        assert client.get(url).status_code == 200, url


def test_path_cannot_leave_the_media_folder(client, admin_user, settings):
    (Path(settings.MEDIA_ROOT).parent / "secret.txt").write_text("secret", encoding="utf-8")
    Path(settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
    client.force_login(admin_user)
    for url in (
        "/media/../secret.txt",
        "/media/%2e%2e/secret.txt",
        "/media/models/../../secret.txt",
    ):
        assert client.get(url).status_code in (400, 404), url


def test_production_settings_load():
    """
    Настройки с DEBUG=False: статику отдаёт WhiteNoise, ошибки пишутся в вывод. Проверяются
    отдельным процессом, потому что настройки читаются один раз при запуске.
    """
    env = {
        **os.environ,
        "DEBUG": "False",
        "SECRET_KEY": "test-only-secret-key-for-production-settings-check-0000",
        "ALLOWED_HOSTS": "erp.example.kz",
        "CSRF_TRUSTED_ORIGINS": "https://erp.example.kz",
    }
    code = (
        "import django; django.setup(); "
        "from django.conf import settings as s; "
        "from django.core.management import call_command; "
        "assert s.MIDDLEWARE[:2] == ['django.middleware.security.SecurityMiddleware', "
        "'whitenoise.middleware.WhiteNoiseMiddleware'], s.MIDDLEWARE[:2]; "
        "assert 'whitenoise' in s.STORAGES['staticfiles']['BACKEND']; "
        "assert s.SESSION_COOKIE_SECURE and s.SECURE_SSL_REDIRECT; "
        "assert s.LOGGING['loggers']['django']['handlers'] == ['console']; "
        "call_command('check', deploy=True, fail_level='ERROR')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=django_settings.BASE_DIR,
        env={**env, "DJANGO_SETTINGS_MODULE": "config.settings"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
