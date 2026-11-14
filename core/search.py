"""
Поиск в списках и выпадающих списках. Не учитывает регистр, разницу «е» и «ё» и знаки
препинания; слова можно вводить в любом порядке и не целиком: «petg, белый», «PETG белый»
и «белый petg» находят один и тот же материал, «пленка» находит «Плёнку».

Запрос делится на слова. Запись подходит, если каждое слово нашлось хотя бы в одном из
полей поиска. И слова, и текст в базе перед сравнением приводятся к одному виду (fold):
строчные буквы, «ё» как «е», без знаков препинания.

Знаки внутри слова сначала просто убираются: код «M-0000001_01» ищется как «m000000101»
и находит только свою запись. Если так не нашлось ничего, знаки считаются пробелами:
«луна-белый» находит «Светильник Луна — Белый».
"""

from django.contrib.admin.utils import lookup_spawns_duplicates
from django.db.models import F, Func, Q, TextField, Value
from django.db.models.functions import Cast, Lower
from django.db.models.lookups import Contains

NBSP = " "

# Знаки, которых для поиска нет
PUNCTUATION = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~«»„“”‘’…–—×·•№"

_DELETE = str.maketrans({"ё": "е", NBSP: " ", **dict.fromkeys(PUNCTUATION)})
_TO_SPACES = str.maketrans({"ё": "е", NBSP: " ", **dict.fromkeys(PUNCTUATION, " ")})

# То же для базы: TRANSLATE заменяет «ё» и неразрывный пробел, остальные знаки удаляет
_SQL_FROM = "ё" + NBSP + PUNCTUATION
_SQL_TO = "е" + " "


def fold(text: str) -> str:
    """Вид для сравнения: строчные буквы, «ё» как «е», без знаков препинания."""
    return (text or "").lower().translate(_DELETE)


def search_words(term: str) -> list[str]:
    """Запрос → слова, знаки удалены: «PETG, Белый» → ["petg", "белый"]."""
    return [word for word in map(fold, (term or "").split()) if word]


def loose_words(term: str) -> list[str]:
    """Запрос → слова, знаки как пробелы: «луна-белый» → ["луна", "белый"]."""
    return (term or "").lower().translate(_TO_SPACES).split()


def folded(source) -> Func:
    """Поле базы (имя или выражение) в виде для сравнения — то же, что fold(), в PostgreSQL."""
    expression = F(source) if isinstance(source, str) else source
    return Func(
        Lower(Cast(expression, TextField())),
        Value(_SQL_FROM),
        Value(_SQL_TO),
        function="TRANSLATE",
        output_field=TextField(),
    )


def words_condition(sources, words: list[str]) -> Q:
    """Каждое слово нашлось хотя бы в одном из полей."""
    texts = [folded(source) for source in sources]
    condition = Q()
    for word in words:
        in_any_field = Q()
        for text in texts:
            in_any_field |= Q(Contains(text, word))
        condition &= in_any_field
    return condition


def search(queryset, sources, term: str):
    """Записи, подходящие под запрос; None — в запросе нет ни одного слова (искать нечего)."""
    words = search_words(term)
    if not words or not sources:
        return None
    result = queryset.filter(words_condition(sources, words))
    loose = loose_words(term)
    if loose != words and not result.exists():
        result = queryset.filter(words_condition(sources, loose))
    return result


class SearchMixin:
    """
    Поиск для списка и для выпадающих списков с подсказками (они ищут через тот же метод).
    Подмешивается перед ModelAdmin и заменяет стандартный поиск Django целиком.

    search_fields — как обычно, имена полей. search_expressions — дополнительные выражения,
    когда на экране текст собирается из нескольких полей (полный код варианта).
    """

    search_expressions = ()

    def get_search_results(self, request, queryset, search_term):
        fields = [field.lstrip("^=@") for field in self.get_search_fields(request)]
        result = search(queryset, [*fields, *self.search_expressions], search_term)
        if result is None:
            return queryset, False
        may_have_duplicates = any(lookup_spawns_duplicates(self.opts, f) for f in fields)
        return result, may_have_duplicates
