"""
Защита данных от ролей без прав: утечка себестоимости через URL-фильтры,
сохранение через страницу истории, генераторы по одному праву просмотра,
изменяющие действия только по POST.
"""

import pytest
from django.contrib.auth.models import Group, User
from django.urls import reverse

from core.demo import load_demo
from core.roles import OBSERVER, SELLER
from costing.services import recalculate_all
from production.models import ProductModel, Variant
from sales.models import CostSnapshot, Product

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo():
    load_demo()
    recalculate_all()


def user_in(group, username):
    user = User.objects.create_user(username, password="x", is_staff=True)
    user.groups.add(Group.objects.get(name=group))
    return user


def test_cost_lookup_is_blocked_for_seller(client, demo):
    client.force_login(user_in(SELLER, "seller"))
    url = reverse("admin:sales_product_changelist")
    # Django считает такой запрос подозрительным и отвечает 400, список не фильтруется
    assert client.get(url + "?cost_cached__gt=8000").status_code == 400
    response = client.get(url + "?o=-5")  # сортировка по скрытой колонке игнорируется
    assert response.status_code == 200


def test_cost_lookup_allowed_for_admin(client, admin_user, demo):
    client.force_login(admin_user)
    url = reverse("admin:sales_product_changelist")
    assert client.get(url + "?cost_cached__gt=8000").status_code == 200


def test_history_form_post_requires_change_permission(client, demo):
    product = Product.objects.get(name="Светильник Луна — Белый")
    version = product.history.first()
    client.force_login(user_in(SELLER, "seller"))
    url = reverse("admin:sales_product_simple_history", args=[product.pk, version.history_id])
    assert client.get(url).status_code == 200
    response = client.post(
        url, {"sku": product.sku, "name": "Взлом", "kind": "single", "status": "draft"}
    )
    assert response.status_code == 403
    product.refresh_from_db()
    assert product.name != "Взлом"


def test_settings_history_page_opens(client, admin_user):
    client.force_login(admin_user)
    url = reverse("admin:core_sitesettings_history", args=[1])
    assert client.get(url).status_code == 200


def test_generators_require_add_permissions(client, demo):
    luna = ProductModel.objects.get(name="Светильник Луна")
    client.force_login(user_in(OBSERVER, "observer"))
    for name in ("create_variants", "create_set", "create_size"):
        url = reverse(f"admin:production_productmodel_{name}", args=[luna.pk])
        assert client.get(url).status_code == 403, name
    variants_before = Variant.objects.count()
    client.post(
        reverse("admin:production_productmodel_create_variants", args=[luna.pk]),
        {"materials": [1], "_form_submitted": "on"},
    )
    assert Variant.objects.count() == variants_before


def test_fix_cost_runs_only_on_post(client, admin_user, demo):
    client.force_login(admin_user)
    product = Product.objects.get(name="Светильник Луна — Белый")
    url = reverse("admin:sales_product_fix_cost_detail", args=[product.pk])
    assert client.get(url).status_code == 200  # диалог, ничего не создано
    assert CostSnapshot.objects.filter(product=product).count() == 0
    response = client.post(url, {"_form_submitted": "on"}, HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    assert response["HX-Redirect"].endswith(f"/admin/sales/product/{product.pk}/change/")
    assert CostSnapshot.objects.filter(product=product).count() == 1


def test_variants_tab_shows_cost_to_admin(client, admin_user, demo):
    client.force_login(admin_user)
    luna = ProductModel.objects.get(name="Светильник Луна")
    html = client.get(
        reverse("admin:production_productmodel_change", args=[luna.pk])
    ).content.decode()
    assert "6 795,75" in html  # себестоимость варианта во вкладке «Варианты»


def test_dialog_shows_validation_error(client, admin_user, demo):
    client.force_login(admin_user)
    luna = ProductModel.objects.get(name="Светильник Луна")
    url = reverse("admin:production_productmodel_create_variants", args=[luna.pk])
    response = client.post(url, {"_form_submitted": "on"})
    assert response.status_code == 200
    assert "Отметьте хотя бы один материал" in response.content.decode()


def test_role_descriptions_are_shown(client, admin_user):
    """Список ролей и карточка пользователя объясняют, кто за что отвечает."""
    from core.roles import ROLE_DESCRIPTIONS

    client.force_login(admin_user)
    roles = client.get(reverse("admin:auth_group_changelist")).content.decode()
    for name, text in ROLE_DESCRIPTIONS.items():
        assert name in roles and text[:40] in roles, name
    group = Group.objects.get(name="Технолог")
    card = client.get(reverse("admin:auth_group_change", args=[group.pk])).content.decode()
    assert "Отвечает за производство и склад" in card
    user_page = client.get(reverse("admin:auth_user_change", args=[admin_user.pk])).content.decode()
    assert "Владелец системы" in user_page and "Только смотрит каталог" in user_page
