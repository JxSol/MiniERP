"""
Поиск в списках и выпадающих списках: без учёта регистра, «е/ё» и знаков препинания,
слова в любом порядке. Один и тот же поиск у всех разделов.
"""

import pytest
from django.contrib import admin
from django.urls import reverse

from catalog.models import Material, MaterialCategory, Unit
from core.demo import load_demo
from core.search import SearchMixin, fold, loose_words, search_words
from costing.services import recalculate_all
from production.models import Variant

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo():
    load_demo()
    recalculate_all()


def found(client, term, name="catalog_material") -> list[str]:
    response = client.get(reverse(f"admin:{name}_changelist"), {"q": term})
    assert response.status_code == 200, term
    return [str(obj) for obj in response.context["cl"].result_list]


def test_fold_and_words():
    assert fold("Плёнка «ЁЛКА»") == "пленка елка"
    assert fold("M-0000001_01") == "m000000101"
    assert search_words("PETG, Белый") == ["petg", "белый"]
    assert search_words("Красный + синий") == ["красный", "синий"]
    assert search_words("«PLA Basic» (белый)") == ["pla", "basic", "белый"]
    assert search_words(" , - ") == [] and search_words("") == []
    # знаки внутри слова: сначала удаляются, а если ничего не нашлось — работают как пробел
    assert search_words("M-0000001_01") == ["m000000101"]
    assert loose_words("M-0000001_01") == ["m", "0000001", "01"]
    assert search_words("petg,белый") == ["petgбелый"]
    assert loose_words("petg,Ёлка") == ["petg", "елка"]


@pytest.mark.parametrize(
    "term",
    [
        "PLA Basic, белый",  # как написано в списке
        "pla basic белый",  # без запятой и строчными
        "белый pla basic",  # в другом порядке
        "PLA BASIC,БЕЛЫЙ",  # запятая без пробела, заглавными
        "basic; бел",  # часть слова
        "«PLA Basic» (белый)",
    ],
)
def test_material_is_found_with_any_punctuation_and_case(client, admin_user, demo, term):
    client.force_login(admin_user)
    assert found(client, term) == ["PLA Basic, белый"]


def test_e_equals_yo(client, admin_user, demo):
    client.force_login(admin_user)
    # в базе «ё», в запросе «е»
    assert found(client, "пленка упаковочная") == ["Плёнка упаковочная 58×58 см"]
    assert found(client, "basic черный") == ["PLA Basic, чёрный"]
    assert found(client, "ПЛЁНКА 120") == ["ВПП плёнка 120 см"]
    # в базе «е», в запросе «ё»
    cord = Material(
        name="Шнур черный", category=MaterialCategory.objects.get(name="Электрика"), unit=Unit.METER
    )
    cord.full_clean(exclude=["name_key"])
    cord.save()
    assert found(client, "чёрный шнур") == ["Шнур черный"]


def test_punctuation_inside_names_and_codes(client, admin_user, demo):
    client.force_login(admin_user)
    # знак между цифрами можно набрать любой или никакой
    assert found(client, "коробка 20*20*25") == ["Коробка 20×20×25"]
    assert found(client, "коробка 20-20-25") == ["Коробка 20×20×25"]
    assert found(client, "58×58") == ["Плёнка упаковочная 58×58 см"]

    # полный код варианта «M-0000001_01» находит только свою запись, а не все похожие
    variant = Variant.objects.filter(model__name="Светильник Луна").order_by("code").first()
    siblings = Variant.objects.filter(model=variant.model)
    assert siblings.count() > 1 and variant.full_code.count("-") == 1
    assert found(client, variant.full_code, "production_variant") == [str(variant)]
    bare = variant.full_code.replace("-", "").replace("_", "").lower()
    assert found(client, bare, "production_variant") == [str(variant)]
    by_model = found(client, variant.model.code, "production_variant")
    assert sorted(by_model) == sorted(str(v) for v in siblings)

    # слова через дефис без пробелов: сначала ищется слитно, потом как два слова
    assert found(client, "луна-белый", "production_variant") == ["Светильник Луна — Белый"]
    assert found(client, "pla-basic,белый") == ["PLA Basic, белый"]


def test_documents_are_found_by_material_with_color(client, admin_user, demo):
    """В закупках, выпусках и движениях материал ищется так же: название и цвет."""
    client.force_login(admin_user)
    assert len(found(client, "PLA Basic, белый", "stock_receipt")) == 1
    # слова относятся к одной строке документа: шёлк и белый лежат в разных строках
    assert found(client, "silk, белый", "stock_receipt") == []
    assert found(client, "pla basic, белый", "stock_productionrun")
    assert found(client, "PLA Basic, белый", "stock_stockmove")
    assert found(client, "PLA Basic, оранжевый", "stock_stockmove") == []


def test_only_punctuation_means_no_search(client, admin_user, demo):
    client.force_login(admin_user)
    assert len(found(client, " , - ")) == Material.objects.count()
    assert found(client, "такого материала нет") == []


def test_dropdown_uses_the_same_search(client, admin_user, demo):
    """Выпадающий список материала в строке закупки ищет так же, как список материалов."""
    client.force_login(admin_user)
    params = {"app_label": "stock", "model_name": "receiptline", "field_name": "material"}
    for term in ("PLA Basic, белый", "белый, basic", "пленка упаковочная"):
        response = client.get(reverse("admin:autocomplete"), {**params, "term": term})
        assert response.status_code == 200, term
        assert len(response.json()["results"]) == 1, term
    archived = Material.objects.get(name="PLA Basic", color__name="Белый")
    archived.is_active = False
    archived.save()
    response = client.get(reverse("admin:autocomplete"), {**params, "term": "PLA Basic, белый"})
    assert response.json()["results"] == []  # архивные в выпадающих списках не предлагаются


def test_every_list_uses_the_same_search(client, admin_user, demo):
    """У каждого раздела с поиском он общий и работает по всем своим полям."""
    client.force_login(admin_user)
    checked = 0
    for model, model_admin in admin.site._registry.items():
        if not model_admin.search_fields:
            continue
        assert isinstance(model_admin, SearchMixin), model
        url = reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
        response = client.get(url, {"q": "тест, ёлка-1 (x)"})
        assert response.status_code == 200, url
        checked += 1
    assert checked > 15
