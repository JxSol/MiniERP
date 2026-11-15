"""
Поиск в списках и выпадающих списках: без учёта регистра, «е/ё» и знаков препинания,
слова в любом порядке, запрос можно набрать не в той раскладке. Один и тот же поиск
у всех разделов.
"""

import pytest
from django.contrib import admin
from django.urls import reverse

from catalog.models import Material, MaterialCategory, Unit
from core.demo import load_demo
from core.search import SearchMixin, attempts, fold, loose_words, retyped, search_words
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


def test_other_layout_readings():
    """Слово читается и как набрано, и так, как его набрали бы в другой раскладке."""
    assert retyped(",tksq") == ["белый"]
    assert retyped("<TKSQ") == ["белый"]  # заглавная «Б» — это Shift и клавиша «,»
    assert retyped("x`hysq") == ["чёрный"]
    assert retyped("зуеп") == ["petg"]
    assert retyped("Ь-0000001_01") == ["m-0000001_01"]
    assert retyped("2026") == []  # цифры в обеих раскладках одни и те же
    # вся таблица: три ряда клавиш и «ё»
    russian = "йцукенгшщзхъ фывапролджэ ячсмитьбю ё"
    english = "qwertyuiop[] asdfghjkl;' zxcvbnm,. `"
    assert retyped(russian) == [english] and retyped(english) == [russian]

    # способы прочитать запрос: сначала как набрано, потом с другой раскладкой
    assert attempts("pla") == [[[("pla",)]], [[("pla",), ("здф",)]]]
    assert attempts("pla ,tksq")[1] == [[("pla",), ("здф",)], [("tksq",), ("белый",)]]
    # слово короче трёх знаков в другой раскладке не угадывается: «шт» — это не «in»
    assert attempts("шт") == [[[("шт",)]]]
    assert attempts("2026") == [[[("2026",)]]]
    assert attempts(" , - ") == []
    # знаки внутри слова: четыре способа, от точного к вольному
    assert attempts("луна-белый") == [
        [[("лунабелый",)]],
        [[("лунабелый",), ("keyftksq",)]],
        [[("луна", "белый")]],
        [[("луна", "белый"), ("keyf", "tksq")]],
    ]


@pytest.mark.parametrize(
    ("term", "expected"),
    [
        (",tksq pla basic", "PLA Basic, белый"),  # «белый» в английской раскладке
        ("PLA BASIC <TKSQ", "PLA Basic, белый"),  # то же заглавными
        ("pla basic, ,tksq", "PLA Basic, белый"),  # с запятой, как написано в списке
        ("здф ифышс белый", "PLA Basic, белый"),  # «pla basic» в русской раскладке
        ("здф-ифышс белый", "PLA Basic, белый"),  # и через дефис
        ("basic x`hysq", "PLA Basic, чёрный"),  # «чёрный» через «ё»
        ("basic xthysq", "PLA Basic, чёрный"),  # и через «е»
        ("gktyrf egfrjdjxyfz", "Плёнка упаковочная 58×58 см"),  # весь запрос не в той раскладке
    ],
)
def test_wrong_keyboard_layout(client, admin_user, demo, term, expected):
    client.force_login(admin_user)
    assert found(client, term) == [expected]


def test_wrong_layout_in_codes_dropdowns_and_global_search(client, admin_user, demo):
    client.force_login(admin_user)
    # код варианта русскими буквами находит только свой вариант, как и набранный верно
    variant = Variant.objects.filter(model__name="Светильник Луна").order_by("code").first()
    assert variant.full_code.startswith("M-")
    typed = variant.full_code.replace("M", "Ь")
    assert found(client, typed, "production_variant") == [str(variant)]
    # «луна-белый» в английской раскладке: слова через дефис без пробелов
    assert found(client, "keyf-,tksq", "production_variant") == ["Светильник Луна — Белый"]
    # русская «х» между цифрами: она на клавише «[», а знаков для поиска нет
    assert found(client, "коробка 20х20х25") == ["Коробка 20×20×25"]

    # выпадающий список материала в строке закупки
    params = {"app_label": "stock", "model_name": "receiptline", "field_name": "material"}
    response = client.get(reverse("admin:autocomplete"), {**params, "term": ",tksq pla basic"})
    assert [item["text"] for item in response.json()["results"]] == ["PLA Basic, белый"]

    # общий поиск по всем разделам (Ctrl+K)
    response = client.get(reverse("admin:search"), {"s": "basic ,tksq"})
    assert response.status_code == 200
    assert "PLA Basic, белый" in response.content.decode()


def test_layout_is_guessed_only_when_nothing_is_found_as_typed(client, admin_user, demo):
    """Другая раскладка — запасной способ: найденное как набрано ею не разбавляется."""
    client.force_login(admin_user)
    category = MaterialCategory.objects.get(name="Электрика")
    for name in ("Здф", "Inox"):
        material = Material(name=name, category=category, unit=Unit.METER)
        material.full_clean(exclude=["name_key"])
        material.save()
    assert found(client, "здф") == ["Здф"]  # а не материалы PLA
    assert "Здф" not in found(client, "pla")
    # «штщч» — это «inox», а «шт» слишком коротко, чтобы читать его как «in»
    assert found(client, "штщч") == ["Inox"]
    assert "Inox" not in found(client, "шт")

    # выпадающий список архивных не предлагает, поэтому и поиску они не мешают: когда «Здф»
    # в архиве, «здф» там читается как «pla»
    def dropdown(term):
        params = {"app_label": "stock", "model_name": "receiptline", "field_name": "material"}
        response = client.get(reverse("admin:autocomplete"), {**params, "term": term})
        return sorted(item["text"] for item in response.json()["results"])

    assert dropdown("здф") == ["Здф"]
    archived = Material.objects.get(name="Здф")
    archived.is_active = False
    archived.save()
    assert dropdown("здф") == dropdown("pla") != []


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
