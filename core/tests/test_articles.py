"""Артикулы: код категории + номер модели + буква варианта, наборы -N, миксы X (ТЗ 6.8а)."""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from core.demo import load_demo, variant_of
from costing.services import recalculate_all
from production import generators
from production.models import ProductCategory, ProductModel, variant_letters
from sales.models import Product, ProductItem, ProductKind

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo():
    load_demo()
    recalculate_all()


def test_variant_letters_skip_confusable_ones():
    assert [variant_letters(n) for n in (1, 2, 8, 9, 23, 24, 25)] == [
        "A",
        "B",
        "H",
        "J",
        "Z",
        "AA",
        "AB",
    ]
    assert "I" not in variant_letters(9) and "O" not in variant_letters(14)


def test_category_code_rules():
    category = ProductCategory(name="Проверка кода", code="TX")
    with pytest.raises(ValidationError, match="I, O и X"):
        category.full_clean(exclude=["name_key"])
    category.code = "IS"
    with pytest.raises(ValidationError):
        category.full_clean(exclude=["name_key"])
    category.code = "QQ"  # свободный код проходит
    category.full_clean(exclude=["name_key"])
    seeded = ProductCategory.objects.get(name="Настольные светильники")
    assert seeded.code == "LT" and seeded.parent.name == "Освещение"
    assert ProductCategory.objects.get(name="Копилки").code == "HPB"
    assert not ProductCategory.objects.filter(code__contains="I").exists()


def test_articles_for_singles_sets_and_mixes(demo):
    luna = ProductModel.objects.get(name="Светильник Луна")
    ball = ProductModel.objects.get(name="Ёлочный шар")
    assert luna.article_base == "LT0001" and ball.article_base == "NT0001"
    assert Product.objects.get(name="Светильник Луна — Белый").article == "LT0001A"
    assert Product.objects.get(name="Светильник Луна — Золотой").article == "LT0001C"
    red_set = generators.create_set(ball, [variant_of(ball, "Красный")], 50)
    assert red_set.article == "NT0001A-N50"
    even = Product.objects.get(name__startswith="Ёлочный шар — микс ровный")
    assert even.article.startswith("NT0001X-N")
    # набор из разных категорий без общего начала кода — XT
    mixed = Product.objects.create(name="Подарочный набор", kind=ProductKind.SET)
    ProductItem.objects.create(product=mixed, variant=variant_of(luna, "Белый"), quantity=1)
    ProductItem.objects.create(product=mixed, variant=variant_of(ball, "Красный"), quantity=2)
    assert mixed.assign_article() == "XT0001-N3"
    # повторный вызов не меняет артикул, а копия модели в той же категории получает номер 2
    assert mixed.assign_article() == "XT0001-N3"
    second = ProductModel.objects.create(name="Светильник Марс", category=luna.category)
    assert second.article_base == "LT0002"


def test_product_without_category_code_has_no_article(demo):
    plain = ProductCategory.objects.create(name="Без кода")
    model = ProductModel.objects.create(name="Безымянное изделие", category=plain)
    assert model.article_base == ""
    variant = model.variants.create(name="Единственный")
    product = generators.create_single_product(variant)
    assert product.article == ""
    assert any("нет артикула" in p for p in product.activation_problems())


def test_article_shown_in_admin(client, admin_user, demo):
    client.force_login(admin_user)
    html = client.get(reverse("admin:sales_product_changelist")).content.decode()
    assert "LT0001A" in html
    product = Product.objects.get(article="LT0001A")
    page = client.get(reverse("admin:sales_product_change", args=[product.pk])).content.decode()
    assert "LT0001A" in page and "SKU-" in page
