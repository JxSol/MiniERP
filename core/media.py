"""
Отдача загруженных файлов: фото и файлов моделей для печати.

Файлы открываются только вошедшим сотрудникам. Ссылки на них легко подобрать
(media/models/M-0000001/…), а файлы моделей — главное, что есть у производства, поэтому
в интернете их нельзя отдавать любому, кто знает адрес. Всё, что относится к деталям, требует
ещё и права на раздел «Детали»: продавец его не имеет.
"""

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.utils.cache import patch_cache_control
from django.views.static import serve

# Начало пути внутри media/ → право, без которого файл не отдаётся
RESTRICTED = {
    "part_files/": "production.view_part",  # файлы моделей для печати и резки
    "parts/": "production.view_part",  # фото деталей
    "thumbs/parts/": "production.view_part",
}

# Браузер хранит файл у себя сутки и не запрашивает его заново при каждом открытии списка.
# Имя загруженного файла не переиспользуется, поэтому устаревшую картинку он не покажет
CACHE_SECONDS = 24 * 60 * 60


@staff_member_required
def protected_media(request, path):
    for prefix, permission in RESTRICTED.items():
        if path.startswith(prefix) and not request.user.has_perm(permission):
            raise PermissionDenied
    response = serve(request, path, document_root=settings.MEDIA_ROOT)
    patch_cache_control(response, private=True, max_age=CACHE_SECONDS)
    return response
