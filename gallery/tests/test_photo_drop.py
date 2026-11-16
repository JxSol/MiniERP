"""
Вкладка «Фото»: область, куда фото перетаскивают, вставляют из буфера обмена или выбирают
сразу несколько (gallery/photo_drop.js). Скрипт добавляет в таблицу фото обычные строки,
поэтому здесь проверяется, что страница даёт ему всё нужное и что сервер принимает
несколько новых фото за одно сохранение. Сам скрипт проверяется в браузере.
"""

import io
import re
from pathlib import Path

import pytest
from django.contrib.auth.models import Group, User
from django.contrib.staticfiles import finders
from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import get_template
from django.urls import reverse
from PIL import Image

from core.demo import load_demo
from core.roles import SELLER
from costing.services import recalculate_all
from gallery.admin import PhotoInline
from gallery.models import MAX_UPLOAD_MB, PHOTO_EXTENSIONS, PhotoKind
from production.models import Part, ProductModel, Variant
from sales.models import Product

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo():
    load_demo()
    recalculate_all()


def image_file(name, image_format="PNG"):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buffer, image_format)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{image_format.lower()}")


def change_url(obj):
    return reverse(f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change", args=[obj.pk])


def test_photo_tab_has_drop_zone_on_every_card(client, admin_user, demo):
    """
    В карточках товара, модели, варианта и детали над таблицей фото есть область для
    перетаскивания, а в самой таблице — всё, на что опирается скрипт: тест упадёт, если тема
    поменяет разметку таблицы.
    """
    for name in ("gallery/photo_drop.js", "gallery/photo_drop.css"):
        assert finders.find(name), name

    client.force_login(admin_user)
    for obj in (
        Product.objects.get(name="Светильник Луна — Белый"),
        ProductModel.objects.get(name="Светильник Луна"),
        Variant.objects.get(model__name="Светильник Луна", name="Белый"),
        Part.objects.get(name="Плафон Луна"),
    ):
        page = client.get(change_url(obj)).content.decode()
        assert 'data-photo-drop="photos"' in page, obj
        assert f'data-max-mb="{MAX_UPLOAD_MB}"' in page
        assert f'data-extensions="{",".join(PHOTO_EXTENSIONS)}"' in page
        assert "gallery/photo_drop.js" in page and "gallery/photo_drop.css" in page
        # таблица фото, ссылка «Добавить ещё», строка-образец с полем файла и корзиной
        assert 'id="photos-data"' in page
        assert re.search(r'<a href="#" class="add-row\b', page)
        assert re.search(r'<tbody class="form-group [^"]*\btemplate empty-form"', page)
        assert 'name="photos-__prefix__-image"' in page
        assert 'name="photos-__prefix__-order"' in page
        assert "field-preview" in page and "field-image" in page
        assert "delete-template" in page
        # вкладка, на которую скрипт переключается после добавления
        assert 'href="#photos"' in page
        # предел числа фото в одной карточке
        limit = re.search(r'name="photos-MAX_NUM_FORMS" value="(\d+)"', page)
        assert limit and int(limit.group(1)) == PhotoInline.max_num == 50


def test_script_reads_what_the_page_gives():
    """Названия, по которым скрипт находит область и её настройки, совпадают с шаблоном."""
    script = Path(finders.find("gallery/photo_drop.js")).read_text(encoding="utf-8")
    template = Path(get_template(PhotoInline.template_before).origin.name).read_text("utf-8")
    for attribute, js_name in (
        ("data-photo-drop", "dataset.photoDrop"),
        ("data-max-mb", "dataset.maxMb"),
        ("data-extensions", "dataset.extensions"),
    ):
        assert attribute in template and js_name in script
    assert 'class="photo-drop-status"' in template and ".photo-drop-status" in script


def test_viewer_has_no_drop_zone(client, demo):
    """Продавец фото только смотрит: области для добавления у него нет."""
    seller = User.objects.create_user("seller", password="x", is_staff=True)
    seller.groups.add(Group.objects.get(name=SELLER))
    client.force_login(seller)
    response = client.get(change_url(Product.objects.get(name="Светильник Луна — Белый")))
    assert response.status_code == 200
    assert "data-photo-drop" not in response.content.decode()


def test_several_photos_saved_at_once(client, admin_user, demo):
    """
    Три фото одним сохранением — так карточку отправляет браузер после перетаскивания и
    вставки: по строке на файл, порядок по возрастанию, у вставленной картинки имя по времени.
    """
    shade = Part.objects.get(name="Плафон Луна")
    position = shade.main_position
    client.force_login(admin_user)
    photos = [
        ("раскладка.png", "PNG", PhotoKind.TECH, "раскладка на столе"),
        ("foto-20261116-134545.png", "PNG", PhotoKind.EXTRA, ""),
        ("готовая.jpg", "JPEG", PhotoKind.EXTRA, "после печати"),
    ]
    data = {
        "name": shade.name,
        "equipment": shade.equipment_id,
        "batch_minutes_0": "0",
        "batch_minutes_1": "8",
        "batch_minutes_2": "0",
        "batch_minutes_3": "0",
        "batch_size": "1",
        "is_active": "on",
        "notes": "",
        "materials-TOTAL_FORMS": "1",
        "materials-INITIAL_FORMS": "1",
        "materials-MIN_NUM_FORMS": "1",
        "materials-0-id": position.pk,
        "materials-0-name": "",
        "materials-0-material": position.material_id,
        "materials-0-batch_qty": "250",
        "materials-0-batch_unit": "",
        "materials-0-slot": "Плафон",
        "operations-TOTAL_FORMS": "0",
        "operations-INITIAL_FORMS": "0",
        "photos-TOTAL_FORMS": str(len(photos)),
        "photos-INITIAL_FORMS": "0",
    }
    for index, (name, image_format, kind, caption) in enumerate(photos):
        data |= {
            f"photos-{index}-id": "",
            f"photos-{index}-part": shade.pk,
            f"photos-{index}-image": image_file(name, image_format),
            f"photos-{index}-kind": kind,
            f"photos-{index}-caption": caption,
            f"photos-{index}-order": str(index),
        }
    response = client.post(change_url(shade), data)
    assert response.status_code == 302, response.content.decode()[:800]

    saved = list(shade.photos.all())
    assert [(Path(p.image.name).name, p.kind, p.caption) for p in saved] == [
        (name, kind, caption) for name, _, kind, caption in photos
    ]
    assert all(p.thumbnail and p.uploaded_by == admin_user for p in saved)
