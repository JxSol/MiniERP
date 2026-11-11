"""
Составные цвета палитры и «Радуга» (ТЗ п. 6.2): состав из обычных цветов, кружок-образец
из секторов, защита от дублей по составу, формы палитры.
"""

import pytest
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.urls import reverse

from catalog.models import (
    RAINBOW_SWATCH,
    Color,
    ColorKind,
    Material,
    MaterialCategory,
    Unit,
    mix_key,
)
from core.admin_utils import color_swatch
from core.roles import TECHNOLOGIST

pytestmark = pytest.mark.django_db

ADD_MIX = "admin:catalog_color_add_mix"


def palette(*names) -> list[Color]:
    return [Color.objects.get(name=name) for name in names]


def post_mix(client, url, colors, name="", **extra):
    data = {"mix": [color.pk for color in colors], "name": name, "order": "30", "is_active": "on"}
    return client.post(url, {**data, "notes": "", **extra})


def test_mix_color_is_built_from_palette_colors():
    red, blue, yellow = palette("Красный", "Синий", "Жёлтый")
    mix = Color.create_mix([blue, red])  # порядок выбора не важен

    assert mix.kind == ColorKind.MIX and mix.is_mix
    assert mix.name == "Красный + синий"  # название по составу, в порядке палитры
    assert mix.order == red.order  # в палитре встаёт рядом с первым цветом состава
    assert mix.components_key == mix_key([red, blue]) == mix_key([blue, red])
    assert mix.mix_colors() == [red, blue]
    assert mix.swatch_css == f"conic-gradient(from 180deg, {red.hex} 0% 50%, {blue.hex} 50% 100%)"
    assert red.swatch_css == red.hex and not red.is_mix  # обычный цвет рисуется как раньше

    html = color_swatch(mix)
    assert 'title="Красный + синий"' in html and "conic-gradient(from 180deg" in html
    assert f"background:{red.hex};" in color_swatch(red, "Свой заголовок")
    assert 'title="Свой заголовок"' in color_swatch(red, "Свой заголовок")

    three = Color.create_mix([red, yellow, blue], name="Светофор")
    assert three.name == "Светофор"
    assert "0% 33.33%" in three.swatch_css and "66.67% 100%" in three.swatch_css

    # Образец составного цвета берётся из палитры: поправили красный — поменялся и он
    red.hex = "#FF0000"
    red.save()
    assert "#FF0000 0% 50%" in Color.objects.get(pk=mix.pk).swatch_css


def test_same_composition_cannot_be_created_twice():
    red, blue, white = palette("Красный", "Синий", "Белый")
    rainbow = Color.objects.get(kind=ColorKind.RAINBOW)
    sunset = Color.create_mix([red, blue], name="Закат")

    with pytest.raises(ValidationError, match="Такой составной цвет уже есть: «Закат»"):
        Color.create_mix([blue, red], name="Совсем другое название")
    with pytest.raises(ValidationError, match="хотя бы два цвета"):
        Color.create_mix([red])
    with pytest.raises(ValidationError, match="хотя бы два цвета"):
        Color.create_mix([red, red])
    with pytest.raises(ValidationError, match="не больше 8 цветов"):
        Color.create_mix(Color.objects.filter(kind=ColorKind.PLAIN)[:9])
    with pytest.raises(ValidationError, match="только из обычных цветов. Не подходят: «Закат»"):
        Color.create_mix([sunset, white])
    with pytest.raises(ValidationError, match="Не подходят: «Радуга»"):
        Color.create_mix([rainbow, white])

    sunset.is_active = False
    sunset.save()
    with pytest.raises(ValidationError, match=r"«Закат» \(в архиве\)"):
        Color.create_mix([red, blue])

    # То же самое держит база, даже если обойти проверку
    with pytest.raises(IntegrityError), transaction.atomic():
        Color.objects.create(
            name="Мимо проверки", kind=ColorKind.MIX, components_key=sunset.components_key
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        Color.objects.create(name="Составной без состава", kind=ColorKind.MIX)
    with pytest.raises(IntegrityError), transaction.atomic():
        Color.objects.create(name="Обычный с составом", components_key="1-2")


def test_rainbow_is_ready_made_color():
    rainbow = Color.objects.get(kind=ColorKind.RAINBOW)
    assert rainbow.name == "Радуга"
    assert rainbow.swatch_css == RAINBOW_SWATCH
    assert rainbow.swatch_css.startswith("conic-gradient(#") and "%" not in rainbow.swatch_css
    assert rainbow.mix_colors() == []
    assert rainbow.order > Color.objects.get(name="Натуральный").order  # в конце палитры
    with pytest.raises(IntegrityError), transaction.atomic():
        Color.objects.create(name="Вторая радуга", kind=ColorKind.RAINBOW)


def test_color_from_mix_cannot_be_deleted():
    red, blue = palette("Красный", "Синий")
    mix = Color.create_mix([red, blue])
    with pytest.raises(ProtectedError):
        red.delete()
    mix.delete()  # сам составной цвет удаляется, цвета палитры остаются
    assert Color.objects.filter(pk__in=[red.pk, blue.pk]).count() == 2
    red.delete()
    assert not Color.objects.filter(pk=red.pk).exists()


def test_palette_admin_adds_and_edits_mix_color(client, admin_user):
    client.force_login(admin_user)
    red, blue, green = palette("Красный", "Синий", "Зелёный")
    rainbow = Color.objects.get(kind=ColorKind.RAINBOW)
    add_url = reverse(ADD_MIX)

    listing = client.get(reverse("admin:catalog_color_changelist")).content.decode()
    assert "Добавить составной цвет" in listing and f'href="{add_url}"' in listing
    assert "плавный спектр" in listing  # «Радуга» уже в палитре

    add_page = client.get(add_url).content.decode()
    assert "Добавить составной цвет" in add_page
    assert 'name="mix"' in add_page and 'name="hex"' not in add_page
    # у каждого цвета в списке выбора — свой кружок-образец
    assert f'<option value="{red.pk}" data-swatch="{red.hex}">Красный</option>' in add_page
    assert "templateResult: withSwatch" in add_page
    assert ">Радуга</option>" not in add_page  # в состав идут только обычные цвета
    plain_page = client.get(reverse("admin:catalog_color_add")).content.decode()
    assert 'name="hex"' in plain_page and 'name="mix"' not in plain_page

    response = post_mix(client, add_url, [blue, red])
    assert response.status_code == 302, response.content.decode()[:800]
    mix = Color.objects.get(name="Красный + синий")
    assert mix.kind == ColorKind.MIX and mix.mix_colors() == [red, blue] and mix.order == 30

    # Порядок не заполнили — цвет встал рядом с первым цветом состава
    white, yellow = palette("Белый", "Жёлтый")
    assert post_mix(client, add_url, [yellow, white], order="").status_code == 302
    assert Color.objects.get(name="Белый + жёлтый").order == white.order

    # Тот же состав под другим названием не проходит, один цвет — тоже
    again = post_mix(client, add_url, [red, blue], name="Закат")
    assert again.status_code == 200
    assert "Такой составной цвет уже есть: «Красный + синий»" in again.content.decode()
    single = post_mix(client, add_url, [red], name="Один")
    assert "В составном цвете должно быть хотя бы два цвета." in single.content.decode()
    assert not Color.objects.filter(name__in=["Закат", "Один"]).exists()

    listing = client.get(reverse("admin:catalog_color_changelist") + "?kind__exact=mix")
    html = listing.content.decode()
    assert "Красный + Синий" in html  # колонка «Образец»: из чего собран
    assert f"conic-gradient(from 180deg, {red.hex} 0% 50%, {blue.hex} 50% 100%)" in html

    change_url = reverse("admin:catalog_color_change", args=[mix.pk])
    change_page = client.get(change_url).content.decode()
    assert 'name="mix"' in change_page and 'name="hex"' not in change_page
    assert f'<option value="{red.pk}" selected data-swatch="{red.hex}">Красный' in change_page
    assert f'<option value="{green.pk}" data-swatch="{green.hex}">Зелёный' in change_page

    # Поменяли состав, название не трогали — оно перестроилось по новому составу
    response = post_mix(client, change_url, [red, green], name="Красный + синий")
    assert response.status_code == 302, response.content.decode()[:800]
    mix.refresh_from_db()
    assert mix.name == "Красный + зелёный" and mix.mix_colors() == [red, green]
    assert mix.components_key == mix_key([red, green])
    # Своё название остаётся, что бы ни стало с составом
    assert post_mix(client, change_url, [red, green], name="Ёлка").status_code == 302
    assert post_mix(client, change_url, [blue, green], name="Ёлка").status_code == 302
    mix.refresh_from_db()
    assert mix.name == "Ёлка" and mix.mix_colors() == [green, blue]

    rainbow_page = client.get(
        reverse("admin:catalog_color_change", args=[rainbow.pk])
    ).content.decode()
    assert "плавный спектр" in rainbow_page
    assert 'name="hex"' not in rainbow_page and 'name="mix"' not in rainbow_page
    # «Радугу» заново не создать, поэтому удалить её нельзя — только в архив
    rainbow_delete = reverse("admin:catalog_color_delete", args=[rainbow.pk])
    assert rainbow_delete not in rainbow_page
    assert client.post(rainbow_delete, {"post": "yes"}).status_code == 403
    assert Color.objects.filter(kind=ColorKind.RAINBOW).exists()
    red_page = client.get(reverse("admin:catalog_color_change", args=[red.pk])).content.decode()
    assert 'name="hex"' in red_page and 'name="mix"' not in red_page


def test_mix_color_in_materials(client, admin_user):
    red, blue = palette("Красный", "Синий")
    mix = Color.create_mix([red, blue], name="Закат")
    silk = Material(
        name="PLA Silk Двухцветный",
        color=mix,
        category=MaterialCategory.objects.get(name="PLA"),
        unit=Unit.GRAM,
    )
    silk.full_clean(exclude=["name_key"])
    silk.save()
    assert silk.full_name == "PLA Silk Двухцветный, закат"

    client.force_login(admin_user)
    listing = client.get(reverse("admin:catalog_material_changelist")).content.decode()
    assert "PLA Silk Двухцветный, закат" in listing
    assert f"conic-gradient(from 180deg, {red.hex} 0% 50%, {blue.hex} 50% 100%)" in listing

    # В карточке материала цвет ищется и по цветам состава: «красн» находит «Закат»
    found = client.get(
        reverse("admin:autocomplete"),
        {"app_label": "catalog", "model_name": "material", "field_name": "color", "term": "красн"},
    ).json()
    assert {item["text"] for item in found["results"]} == {"Красный", "Закат"}


def test_only_admin_adds_mix_colors(client):
    technologist = User.objects.create_user("tech", password="x", is_staff=True)
    technologist.groups.add(Group.objects.get(name=TECHNOLOGIST))
    client.force_login(technologist)
    listing = client.get(reverse("admin:catalog_color_changelist"))
    assert listing.status_code == 200
    assert f'href="{reverse(ADD_MIX)}"' not in listing.content.decode()  # кнопки нет
    assert client.get(reverse(ADD_MIX)).status_code == 403
