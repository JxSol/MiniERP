"""
Дымовые проверки интерфейса: все страницы открываются, а продавец не видит
себестоимость ни в списке, ни в карточке товара.
"""

import pytest
from django.contrib.auth.models import Group, User
from django.urls import reverse

from core.demo import load_demo
from core.roles import SELLER, TECHNOLOGIST
from costing.services import recalculate_all
from production.models import Part, ProductModel, Variant
from sales.models import Product

pytestmark = pytest.mark.django_db

CHANGELISTS = [
    "sales_product",
    "production_productmodel",
    "production_variant",
    "production_part",
    "catalog_material",
    "catalog_color",
    "catalog_equipment",
    "catalog_operationtype",
    "catalog_materialcategory",
    "production_productcategory",
    "core_tag",
    "core_size",
]


@pytest.fixture
def demo():
    load_demo()
    recalculate_all()


def user_in(group, username):
    user = User.objects.create_user(username, password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    return user


def test_admin_pages_open(client, admin_user, demo):
    client.force_login(admin_user)
    for name in CHANGELISTS:
        assert client.get(reverse(f"admin:{name}_changelist")).status_code == 200, name
    for obj in [
        Product.objects.get(sku="LUNA-WH"),
        ProductModel.objects.get(code="LUNA"),
        Variant.objects.get(model__code="LUNA", code="WH"),
        Part.objects.get(code="LUNA-SHADE"),
    ]:
        url = reverse(f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change", args=[obj.pk])
        assert client.get(url).status_code == 200, url
    settings_page = client.get(reverse("admin:core_sitesettings_changelist"), follow=True)
    assert settings_page.status_code == 200


def test_seller_does_not_see_cost(client, demo):
    client.force_login(user_in(SELLER, "seller"))
    product = Product.objects.get(sku="LUNA-WH")
    listing = client.get(reverse("admin:sales_product_changelist")).content.decode()
    page = client.get(reverse("admin:sales_product_change", args=[product.pk])).content.decode()
    for html in (listing, page):
        assert "LUNA-WH" in html
        assert "7 988" not in html
        assert "Себестоимость" not in html
    assert client.get(reverse("admin:catalog_material_changelist")).status_code == 403
    assert client.get(reverse("admin:production_productmodel_changelist")).status_code == 403


def test_technologist_sees_cost_but_not_palette_editing(client, demo):
    client.force_login(user_in(TECHNOLOGIST, "tech"))
    product = Product.objects.get(sku="LUNA-WH")
    page = client.get(reverse("admin:sales_product_change", args=[product.pk])).content.decode()
    assert "7 988,66" in page
    assert client.get(reverse("admin:catalog_color_add")).status_code == 403
    assert client.get(reverse("admin:catalog_color_changelist")).status_code == 200
