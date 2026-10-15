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
    regex=r"^[A-Z0-9]+(-[A-Z0-9]+)*$",
    message="Только заглавная латиница, цифры и дефис между ними, например LUNA-WH.",
)


class CodeField(models.CharField):
    """Код или артикул: латиница в верхнем регистре, цифры, дефис."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 40)
        kwargs.setdefault("validators", [code_validator])
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        return clean_name(value).upper().replace(" ", "-") if value else value

    def pre_save(self, model_instance, add):
        value = self.to_python(getattr(model_instance, self.attname))
        setattr(model_instance, self.attname, value)
        return value


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
    code = CodeField(
        "Код", unique=True, max_length=10, help_text="Добавляется к коду модели: KONSTR-XL."
    )
    order = models.PositiveIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Размер"
        verbose_name_plural = "Размеры"
        ordering = ["order", "name"]
        constraints = [models.UniqueConstraint(fields=["name_key"], name="core_size_name_uniq")]
