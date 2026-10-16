"""
Сквозные проверки на демо-данных из ТЗ: себестоимость, генераторы, фото,
защита от ошибок и роли.
"""

import io
import re
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
from production.models import Part, ProductModel, Variant
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
    product = Product.objects.get(name="Светильник Луна — Белый")
    assert money(product.cost_direct_cached) == D("7262.42")
    assert money(product.cost_cached) == D("7988.66")


def test_gold_variant_is_more_expensive(demo):
    assert money(Variant.objects.get(model__name="Светильник Луна", name="Белый").cost_cached) == D(
        "6795.75"
    )
    assert money(
        Variant.objects.get(model__name="Светильник Луна", name="Золотой").cost_cached
    ) == D("7616.80")


def test_even_mix_set_contains_25_of_each(demo):
    product = Product.objects.get(name="Ёлочный шар — микс ровный (4 цв.), 100 шт")
    assert sorted(i.quantity for i in product.items.all()) == [25, 25, 25, 25]
    assert (
        product.cost_cached > Product.objects.get(name="Ёлочный шар — Красный, 100 шт").cost_cached
    )


def test_price_change_updates_current_but_not_fixed_cost(demo):
    product = Product.objects.get(name="Светильник Луна — Белый")
    snapshot = fix_cost(product)
    offer = Material.objects.get(name="PLA Basic", color__name="Белый").offers.get()
    offer.pack_price = D(11000)
    offer.save()
    recalculate_all()
    product.refresh_from_db()
    snapshot.refresh_from_db()
    assert product.cost_cached > snapshot.total
    assert money(snapshot.total) == D("7988.66")
    assert offer.price_history.count() == 2


def test_average_price_over_active_offers(demo):
    """Цена материала — среднее по актуальным предложениям; неактуальные не считаются."""
    from catalog.models import MaterialOffer, Supplier

    material = Material.objects.get(name="PLA Basic", color__name="Белый")
    MaterialOffer.objects.create(
        material=material,
        supplier=Supplier.objects.get(name="Ozon"),
        pack_size=D(1000),
        pack_price=D(11000),
    )
    assert material.unit_price == D(10)
    MaterialOffer.objects.create(
        material=material, pack_size=D(1000), pack_price=D(50000), is_active=False
    )
    assert material.unit_price == D(10)


def test_random_variant_uses_line_average(demo):
    """«Ассорти» считается по среднему всех цветов линейки PLA Basic."""
    from decimal import Decimal

    ball = demo["ball"]
    red_offer = Material.objects.get(name="PLA Basic", color__name="Красный").offers.get()
    red_offer.pack_price = D(13000)
    red_offer.save()
    recalculate_all()
    red = ball.variants.get(name="Красный")
    green = ball.variants.get(name="Зелёный")
    mix = ball.variants.get(name="Ассорти")
    red.refresh_from_db(), green.refresh_from_db(), mix.refresh_from_db()
    assert red.cost_cached > mix.cost_cached > green.cost_cached
    assert isinstance(mix.cost_cached, Decimal)


def test_snapshot_is_immutable(demo):
    snapshot = fix_cost(Product.objects.get(name="Светильник Луна — Белый"))
    with pytest.raises(ValidationError):
        snapshot.save()
    with pytest.raises(ValidationError):
        snapshot.delete()


# --- Генераторы ---


def test_generators_created_variants_and_products(demo):
    luna = demo["luna"]
    assert set(luna.variants.values_list("code", flat=True)) == {"01", "02", "03"}
    assert Product.objects.filter(name__startswith="Светильник Луна — ").count() == 3
    gold = luna.variants.get(name="Золотой")
    assert {s.material.name for s in gold.slots.all()} == {"PLA Silk"}


def test_uneven_mix_is_rejected(demo):
    ball = demo["ball"]
    variants = list(ball.variants.filter(name__in=["Красный", "Зелёный", "Фиолетовый"]))
    with pytest.raises(ValidationError):
        generators.create_set(ball, variants, 100)


def test_size_copies_composition_and_variants(demo):
    from core.models import Size

    xl = generators.create_size(demo["luna"], Size.objects.get(name="XL"))
    assert re.fullmatch(r"M-\d{7}", xl.code)
    assert xl.model_parts.count() == 3
    assert set(xl.variants.values_list("code", flat=True)) == {"01", "02", "03"}
    assert Product.objects.filter(name="Светильник Луна XL — Золотой").exists()
    with pytest.raises(ValidationError):
        generators.create_size(xl, Size.objects.get(name="XL"))


# --- Фото ---


def test_photo_rules(demo):
    product = Product.objects.get(name="Светильник Луна — Белый")
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
    product = Product.objects.get(name="Светильник Луна — Чёрный")
    assert main_photo_for_product(product) is None
    photo = Photo.objects.create(model=demo["luna"], image=image_file())
    assert main_photo_for_product(product) == photo


def test_tech_photo_not_allowed_for_product(demo):
    photo = Photo(
        product=Product.objects.get(name="Светильник Луна — Белый"),
        kind=PhotoKind.TECH,
        image=image_file(),
    )
    with pytest.raises(ValidationError):
        photo.full_clean()


# --- Защита от ошибок ---


def test_duplicate_color_name_is_rejected():
    color = Color(name="  золотой ")
    with pytest.raises(ValidationError):
        color.full_clean()


def test_codes_are_generated_by_formula(demo):
    luna = demo["luna"]
    assert re.fullmatch(r"M-\d{7}", luna.code)
    assert re.fullmatch(r"C-FDM-\d{7}", Part.objects.get(name="Плафон Луна").code)
    assert re.fullmatch(r"C-CO2-\d{7}", Part.objects.get(name="Подставка Луна").code)
    variant = Variant.objects.create(model=luna, name="Синяя")
    assert variant.code == "04" and variant.full_code == f"{luna.code}_04"
    product = Product.objects.get(name="Светильник Луна — Белый")
    assert re.fullmatch(r"SKU-\d{7}", product.sku)


def test_technology_change_is_a_new_part(demo):
    from catalog.models import Equipment

    shade = Part.objects.get(name="Плафон Луна")
    laser = Equipment.objects.get(name="Лазер CO₂ 60 Вт")
    shade.equipment = laser
    shade.material = Material.objects.get(name="Фанера 4 мм")
    with pytest.raises(ValidationError, match="новая деталь"):
        shade.full_clean()
    copy = generators.copy_part(Part.objects.get(name="Плафон Луна"), laser)
    assert copy.code.startswith("C-CO2-") and copy.equipment == laser


def test_ean13_check_digit():
    validate_barcode("4006381333931")
    with pytest.raises(ValidationError):
        validate_barcode("4006381333932")


def test_activation_requires_photo_and_dimensions(demo):
    product = Product.objects.get(name="Светильник Луна — Белый")
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


def test_model_calculated_weight_counts_only_gram_parts(demo):
    luna = ProductModel.objects.get(name="Светильник Луна")
    # плафон 250 г × 1 + фиксатор 5 г × 2; фанерная подставка считается в листах — не входит
    assert luna.calculated_weight_g() == D(260)
    luna.weight_g, luna.length_cm = 270, D("12.5")
    luna.full_clean(exclude=["name_key"])
