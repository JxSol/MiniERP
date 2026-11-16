from django.conf import settings
from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from core.media import protected_media

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    # Фото и файлы моделей — только вошедшим сотрудникам, и на сервере, и при разработке
    path(f"{settings.MEDIA_URL.strip('/')}/<path:path>", protected_media, name="media"),
]
