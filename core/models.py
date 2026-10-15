"""
Общие вещи: базовые классы моделей, нормализация названий, общие настройки,
метки и размеры.
"""

import re
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from simple_history.models import HistoricalRecords

# --- Нормализация названий и кодов (защита от дублей, ТЗ п. 5.6) ---------------


def clean_name(value: str) -> str:
    """Обрезает пробелы по краям и схлопывает повторяющиеся пробелы."""
    return re.sub(r"\s+", " ", value or "").strip()


def name_key(value: str) -> str:
    """Ключ сравнения: без учёта регистра, пробелов и ё/е."""
    return clean_name(value).lower().replace("ё", "е")


code_validator = RegexValidator(
    regex=r"^[A-Z0-9]+(-[A-Z0-9]+)*(_[0-9]+)?$",
    message="Только заглавная латиница, цифры и дефис, например C-FDM-0000001.",
)

CODE_DIGITS = 7


class CodeField(models.CharField):
    """
    Код записи. Генерируется системой по формуле (ТЗ п. 5.1), редактировать нельзя:
    C-FDM-0000001 (деталь), M-0000001 (модель), M-0000001_01 (вариант), SKU-0000001 (товар).
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 40)
        kwargs.setdefault("validators", [code_validator])
        kwargs.setdefault("editable", False)
        kwargs.setdefault("blank", True)
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        return clean_name(value).upper().replace(" ", "-") if value else value

    def pre_save(self, model_instance, add):
        value = self.to_python(getattr(model_instance, self.attname))
        setattr(model_instance, self.attname, value)
        return value


class CodeCounter(models.Model):
    """Счётчики для кодов: по одному на префикс (C-FDM, M, SKU…)."""

    prefix = models.CharField(max_length=20, primary_key=True)
    value = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Счётчик кодов"
        verbose_name_plural = "Счётчики кодов"

    def __str__(self):
        return f"{self.prefix}: {self.value}"


def next_code(prefix: str) -> str:
    """Следующий код вида {prefix}-0000001. Безопасно при одновременных запросах."""
    from django.db import transaction

    with transaction.atomic():
        counter, _ = CodeCounter.objects.select_for_update().get_or_create(prefix=prefix)
        counter.value += 1
        counter.save(update_fields=["value"])
    return f"{prefix}-{counter.value:0{CODE_DIGITS}d}"


def percent_field(verbose_name, **kwargs):
    return models.DecimalField(
        verbose_name,
        max_digits=5,
        decimal_places=2,
        validators=[MinValueValidator(Decimal(0)), MaxValueValidator(Decimal(99))],
        **kwargs,
    )


def money_field(verbose_name, **kwargs):
    kwargs.setdefault("validators", [MinValueValidator(Decimal(0))])
    return models.DecimalField(verbose_name, max_digits=14, decimal_places=2, **kwargs)


def positive_decimal_field(verbose_name, **kwargs):
    return models.DecimalField(
        verbose_name,
        max_digits=14,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
        **kwargs,
    )


# --- Базовые классы ----------------------------------------------------------------


class ArchivableModel(models.Model):
    """Ничего не удаляется насовсем: записи отправляются в архив (ТЗ п. 5.7)."""

    is_active = models.BooleanField(
        "Активно", default=True, help_text="Архивные записи скрываются из списков выбора."
    )
    notes = models.TextField("Примечание", blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Изменено", auto_now=True)

    class Meta:
        abstract = True


class UniqueNameModel(ArchivableModel):
    """
    Название, уникальное без учёта регистра, пробелов и ё/е.
    name_scope — поля, внутри которых действует уникальность (например, родитель).
    """

    name = models.CharField("Название", max_length=150)
    name_key = models.CharField(max_length=150, editable=False, db_index=True)

    name_scope: tuple[str, ...] = ()

    class Meta:
        abstract = True

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        self.name = clean_name(self.name)
        if not self.name:
            return
        qs = type(self)._default_manager.filter(name_key=name_key(self.name))
        for field in self.name_scope:
            qs = qs.filter(**{field: getattr(self, field)})
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        existing = qs.first()
        if existing:
            raise ValidationError(
                {"name": f"Уже есть запись «{existing.name}». Используйте её вместо новой."}
            )

    def save(self, *args, **kwargs):
        self.name = clean_name(self.name)
        self.name_key = name_key(self.name)
        super().save(*args, **kwargs)


# --- Общие настройки ---------------------------------------------------------------


class SiteSettings(models.Model):
    """Единственная запись с общими настройками (ТЗ п. 6.1)."""

    electricity_tariff = money_field("Тариф электроэнергии, ₸/кВт·ч", default=Decimal(30))
    labor_rate = money_field("Ставка труда по умолчанию, ₸/ч", default=Decimal(2000))
    default_scrap_percent = percent_field("% брака по умолчанию", default=Decimal(5))
    overhead_percent = percent_field("Накладные, % от прямой себестоимости", default=Decimal(10))
    tax_percent = percent_field("Налог, % от выручки", default=Decimal(4))
    min_margin_percent = percent_field("Минимальная маржа, %", default=Decimal(20))
    target_margin_percent = percent_field("Целевая маржа, %", default=Decimal(30))
    cost_growth_alert_percent = percent_field(
        "Порог «себестоимость выросла», %", default=Decimal(5)
    )
    max_seller_discount_percent = percent_field(
        "Максимальная скидка продавца, %", default=Decimal(10)
    )
    site_url = models.URLField(
        "Адрес системы для QR-ссылок", blank=True, help_text="Например, https://erp.example.kz"
    )

    history = HistoricalRecords()

    class Meta:
        verbose_name = "Общие настройки"
        verbose_name_plural = "Общие настройки"

    def __str__(self):
        return "Общие настройки"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Общие настройки удалить нельзя.")

    @classmethod
    def load(cls) -> "SiteSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# --- Метки и размеры ---------------------------------------------------------------


class Tag(UniqueNameModel):
    class Meta:
        verbose_name = "Метка"
        verbose_name_plural = "Метки"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["name_key"], name="core_tag_name_uniq")]


class Size(UniqueNameModel):
    order = models.PositiveIntegerField("Порядок", default=0, help_text="Порядок в списках выбора.")

    class Meta:
        verbose_name = "Размер"
        verbose_name_plural = "Размеры"
        ordering = ["order", "name"]
        constraints = [models.UniqueConstraint(fields=["name_key"], name="core_size_name_uniq")]
