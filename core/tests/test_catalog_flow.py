"""
Сквозные проверки на демо-данных из ТЗ: себестоимость, генераторы, фото,
защита от ошибок и роли.
"""

import io
from decimal import Decimal as D

import pytest
from django.contrib.auth.models import Group, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from catalog.models import Color, Material
from core.demo import load_demo
from core.roles import SELLER, TECHNOLOGIST
from costing.services import fix_cost, recalculate_all
from gallery.models import Photo, PhotoKind, main_photo_for_product
from production import generators
from production.models import Variant
from sales.models import Product, ProductStatus, validate_barcode

pytestmark = pytest.mark.django_db


def money(value):
    return value.quantize(D("0.01"))


@pytest.fixture
def demo():
    data = load_demo()
    recalculate_all()
    return data


def image_file(name="photo.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), "red").save(buffer, "PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


# --- Себестоимость ---


def test_luna_white_matches_tz(demo):
    product = Product.objects.get(sku="LUNA-WH")
    assert money(product.cost_direct_cached) == D("7262.42")
    assert money(product.cost_cached) == D("7988.66")


def test_gold_variant_is_more_expensive(demo):
    assert money(Variant.objects.get(model__code="LUNA", code="WH").cost_cached) == D("6795.75")
    assert money(Variant.objects.get(model__code="LUNA", code="GD").cost_cached) == D("7616.80")


def test_even_mix_set_contains_25_of_each(demo):
    product = Product.objects.get(sku="BALL-MIX4-100")
    assert sorted(i.quantity for i in product.items.all()) == [25, 25, 25, 25]
    assert product.cost_cached > Product.objects.get(sku="BALL-RD-100").cost_cached


def test_price_change_updates_current_but_not_fixed_cost(demo):
    product = Product.objects.get(sku="LUNA-WH")
    snapshot = fix_cost(product)
    pla = Material.objects.get(name="PLA Basic")
    pla.pack_price = D(11000)
    pla.save()
    recalculate_all()
    product.refresh_from_db()
    snapshot.refresh_from_db()
    assert product.cost_cached > snapshot.total
    assert money(snapshot.total) == D("7988.66")
    assert pla.price_history.count() == 2


def test_snapshot_is_immutable(demo):
    snapshot = fix_cost(Product.objects.get(sku="LUNA-WH"))
    with pytest.raises(ValidationError):
        snapshot.save()
    with pytest.raises(ValidationError):
        snapshot.delete()


# --- Генераторы ---


def test_generators_created_variants_and_products(demo):
    luna = demo["luna"]
    assert set(luna.variants.values_list("code", flat=True)) == {"WH", "BK", "GD"}
    assert Product.objects.filter(sku__in=["LUNA-WH", "LUNA-BK", "LUNA-GD"]).count() == 3
    gold = luna.variants.get(code="GD")
    assert {s.material.name for s in gold.slots.all()} == {"Silk PLA"}


def test_uneven_mix_is_rejected(demo):
    ball = demo["ball"]
    variants = list(ball.variants.filter(code__in=["RD", "GN", "VT"]))
    with pytest.raises(ValidationError):
        generators.create_set(ball, variants, 100)


def test_size_copies_composition_and_variants(demo):
    from core.models import Size

    xl = generators.create_size(demo["luna"], Size.objects.get(code="XL"))
    assert xl.code == "LUNA-XL"
    assert xl.model_parts.count() == 3
    assert set(xl.variants.values_list("code", flat=True)) == {"WH", "BK", "GD"}
    assert Product.objects.filter(sku="LUNA-XL-GD").exists()
    with pytest.raises(ValidationError):
        generators.create_size(xl, Size.objects.get(code="XL"))


# --- Фото ---


def test_photo_rules(demo):
    product = Product.objects.get(sku="LUNA-WH")
    first = Photo.objects.create(product=product, image=image_file())
    assert first.kind == PhotoKind.MAIN  # первое фото становится главным
    second = Photo.objects.create(product=product, image=image_file(), kind=PhotoKind.MAIN)
    first.refresh_from_db()
    assert first.kind == PhotoKind.EXTRA  # новое главное понижает прежнее
    second.delete()
    first.refresh_from_db()
    assert first.kind == PhotoKind.MAIN  # после удаления главного главным становится следующее
    assert first.thumbnail


def test_photo_inherited_from_model(demo):
    product = Product.objects.get(sku="LUNA-BK")
    assert main_photo_for_product(product) is None
    photo = Photo.objects.create(model=demo["luna"], image=image_file())
    assert main_photo_for_product(product) == photo


def test_tech_photo_not_allowed_for_product(demo):
    photo = Photo(
        product=Product.objects.get(sku="LUNA-WH"), kind=PhotoKind.TECH, image=image_file()
    )
    with pytest.raises(ValidationError):
        photo.full_clean()


# --- Защита от ошибок ---


def test_duplicate_color_name_is_rejected():
    color = Color(name="  золотой ", code="GD2")
    with pytest.raises(ValidationError):
        color.full_clean()


def test_code_is_uppercased_and_validated(demo):
    variant = Variant(model=demo["luna"], name="Синяя", code="bl")
    variant.full_clean()
    assert variant.code == "BL"
    with pytest.raises(ValidationError):
        Variant(model=demo["luna"], name="Зелёная", code="зел").full_clean()


def test_ean13_check_digit():
    validate_barcode("4006381333931")
    with pytest.raises(ValidationError):
        validate_barcode("4006381333932")


def test_activation_requires_photo_and_dimensions(demo):
    product = Product.objects.get(sku="LUNA-WH")
    problems = product.activation_problems()
    assert any("фото" in p for p in problems)
    assert any("габариты" in p for p in problems)
    Photo.objects.create(product=product, image=image_file())
    product.length_cm = product.width_cm = product.height_cm = D(20)
    product.weight_g = 900
    product.status = ProductStatus.ACTIVE
    product.save()
    assert product.activation_problems() == []


# --- Роли ---


def test_seller_cannot_see_cost(demo):
    seller = User.objects.create_user("seller", is_staff=True)
    seller.groups.add(Group.objects.get(name=SELLER))
    technologist = User.objects.create_user("tech", is_staff=True)
    technologist.groups.add(Group.objects.get(name=TECHNOLOGIST))
    assert seller.has_perm("sales.view_product")
    assert not seller.has_perm("sales.view_cost")
    assert not seller.has_perm("catalog.view_material")
    assert technologist.has_perm("sales.view_cost")
    assert not technologist.has_perm("catalog.add_color")  # палитру ведёт только администратор
    assert not technologist.has_perm("catalog.delete_material")  # только архив
