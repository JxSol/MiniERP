"""
Поиск в списках и выпадающих списках. Не учитывает регистр, разницу «е» и «ё» и знаки
препинания; слова можно вводить в любом порядке и не целиком: «petg, белый», «PETG белый»
и «белый petg» находят один и тот же материал, «пленка» находит «Плёнку».

Запрос делится на слова. Запись подходит, если каждое слово нашлось хотя бы в одном из
полей поиска. И слова, и текст в базе перед сравнением приводятся к одному виду (fold):
строчные буквы, «ё» как «е», без знаков препинания.

Запрос читается четырьмя способами, от точного к вольному (attempts); в ответ идёт первый,
по которому что-то нашлось:
1. Как набрано, знаки внутри слова убраны: код «M-0000001_01» ищется как «m000000101»
   и находит только свою запись.
2. То же, но любое слово может быть набрано не в той раскладке: «,tksq» — это «белый»,
   «зуеп» — это «petg».
3. Как набрано, знаки внутри слова считаются пробелами: «луна-белый» находит
   «Светильник Луна — Белый».
4. То же с другой раскладкой.
"""

from functools import reduce
from operator import and_, or_

from django.contrib.admin.utils import lookup_spawns_duplicates
from django.db.models import F, Func, Q, TextField, Value
from django.db.models.functions import Cast, Lower
from django.db.models.lookups import Contains

NBSP = "\u00a0"  # неразрывный пробел

# Знаки, которых для поиска нет
PUNCTUATION = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~«»„“”‘’…–—×·•№"

_DELETE = str.maketrans({"ё": "е", NBSP: " ", **dict.fromkeys(PUNCTUATION)})
_TO_SPACES = str.maketrans({"ё": "е", NBSP: " ", **dict.fromkeys(PUNCTUATION, " ")})

# То же для базы: TRANSLATE заменяет «ё» и неразрывный пробел, остальные знаки удаляет
_SQL_FROM = "ё" + NBSP + PUNCTUATION
_SQL_TO = "е" + " "

# Клавиши английской раскладки и буквы, которые те же клавиши дают в русской. В конце —
# семь клавиш с Shift: в английской раскладке это другой знак («<» — заглавная «Б»)
_ENGLISH_KEYS = "qwertyuiop[]asdfghjkl;'zxcvbnm,.`" + '{}:"<>~'
_RUSSIAN_KEYS = "йцукенгшщзхъфывапролджэячсмитьбюё" + "хъжэбюё"
_TO_RUSSIAN = str.maketrans(_ENGLISH_KEYS, _RUSSIAN_KEYS)
_TO_ENGLISH = str.maketrans(_RUSSIAN_KEYS[:33], _ENGLISH_KEYS[:33])

# Слово в другой раскладке короче трёх знаков не угадывается: иначе «шт» — это ещё и «in»,
# и по нему нашлось бы всё, где есть эти две буквы
MIN_RETYPED = 3

Reading = tuple[str, ...]  # прочтение слова запроса: части, которые должны найтись все
Attempt = list[list[Reading]]  # способ прочитать запрос: у каждого слова свои прочтения


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


def retyped(word: str) -> list[str]:
    """Слово, как если бы его набирали в другой раскладке: «,tksq» → ["белый"]."""
    word = word.lower()
    others = (word.translate(_TO_RUSSIAN), word.translate(_TO_ENGLISH))
    return [other for other in others if other != word]


def _readings(word: str, split, other_layout: bool) -> list[Reading]:
    """Прочтения одного слова: как набрано и, если просили, в другой раскладке."""
    readings = [tuple(split(word))]
    if other_layout:
        guesses = (tuple(split(other)) for other in retyped(word))
        readings += [guess for guess in guesses if sum(map(len, guess)) >= MIN_RETYPED]
    return [reading for reading in dict.fromkeys(readings) if reading]


def attempts(term: str) -> list[Attempt]:
    """Способы прочитать запрос, от точного к вольному; одинаковые не повторяются."""
    words = (term or "").lower().split()
    result = []
    for split, other_layout in (
        (search_words, False),
        (search_words, True),
        (loose_words, False),
        (loose_words, True),
    ):
        attempt = [found for word in words if (found := _readings(word, split, other_layout))]
        if attempt and attempt not in result:
            result.append(attempt)
    return result


def condition(sources, attempt: Attempt) -> Q:
    """Каждое слово запроса нашлось: в любом своём прочтении, все его части, в любом поле."""
    texts = [folded(source) for source in sources]

    def every(conditions) -> Q:
        return reduce(and_, conditions, Q())

    def some(conditions) -> Q:
        return reduce(or_, conditions, Q())

    return every(
        some(
            every(some(Q(Contains(text, part)) for text in texts) for part in reading)
            for reading in readings
        )
        for readings in attempt
    )


def search(queryset, sources, term: str):
    """Записи, подходящие под запрос; None — в запросе нет ни одного слова (искать нечего)."""
    if not search_words(term) or not sources:
        return None
    *earlier, last = attempts(term)
    for attempt in earlier:
        result = queryset.filter(condition(sources, attempt))
        if result.exists():
            return result
    return queryset.filter(condition(sources, last))


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
