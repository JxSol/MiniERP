"""
Товары (SKU): поштучные и наборы, их упаковка, комплектация и снимки
себестоимости (ТЗ п. 6.8). Каналы продаж и розница — этап 2.
"""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, RegexValidator
from django.db import models
from simple_history.models import HistoricalRecords

from catalog.models import (
    EnteredUnitMixin,
    Material,
    MaterialKind,
    OperationLevel,
    OperationType,
)
from core.models import (
    CodeField,
    Tag,
    UniqueNameModel,
    money_field,
    next_code,
    next_number,
    positive_decimal_field,
)
from production.models import Variant


def validate_barcode(value: str):
    if not value.isdigit() or not 8 <= len(value) <= 14:
        raise ValidationError("Штрихкод — от 8 до 14 цифр.")
    if len(value) == 13:
        digits = [int(c) for c in value]
        check = (10 - sum(d * (3 if i % 2 else 1) for i, d in enumerate(digits[:12])) % 10) % 10
        if check != digits[12]:
            raise ValidationError("Неверная контрольная цифра EAN-13 — проверьте, нет ли опечатки.")


class ProductKind(models.TextChoices):
    SINGLE = "single", "Поштучный"
    SET = "set", "Набор"


class ProductStatus(models.TextChoices):
    DRAFT = "draft", "Черновик"
    ACTIVE = "active", "Активен"
    RETIRED = "retired", "Снят с продажи"


class Product(UniqueNameModel):
    sku = CodeField("Код (SKU)", unique=True)  # SKU-0000001, служебный код базы и QR
    article = models.CharField(
        "Артикул",
        max_length=20,
        blank=True,
        validators=[
            RegexValidator(r"^[A-Z0-9-]{3,20}$", "Латинские заглавные буквы, цифры и дефис.")
        ],
        help_text="Собирается сам: код категории + номер модели + буква варианта (TE0001A), "
        "у наборов -N и количество. Можно поправить, повтор не сохранится.",
    )
    kind = models.CharField(
        "Вид", max_length=10, choices=ProductKind.choices, default=ProductKind.SINGLE
    )
    status = models.CharField(
        "Статус",
        max_length=10,
        choices=ProductStatus.choices,
        default=ProductStatus.DRAFT,
        help_text="Активным товар становится, только когда заполнено всё обязательное.",
    )
    tags = models.ManyToManyField(Tag, verbose_name="Метки", blank=True)
    common_barcode = models.CharField(
        "Общий штрихкод",
        max_length=14,
        blank=True,
        validators=[validate_barcode],
        help_text="Используется площадками, у которых свой штрихкод не указан.",
    )
    length_cm = models.DecimalField(
        "Длина в упаковке, см",
        max_digits=7,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.1"))],
    )
    width_cm = models.DecimalField(
        "Ширина в упаковке, см",
        max_digits=7,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.1"))],
    )
    height_cm = models.DecimalField(
        "Высота в упаковке, см",
        max_digits=7,
        decimal_places=1,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.1"))],
    )
    weight_g = models.PositiveIntegerField(
        "Масса в упаковке, г", null=True, blank=True, validators=[MinValueValidator(1)]
    )
    description = models.TextField("Описание", blank=True, help_text="Пусто — из модели.")

    cost_direct_cached = models.DecimalField(
        "Прямая себестоимость, ₸", max_digits=14, decimal_places=4, null=True, editable=False
    )
    cost_cached = models.DecimalField(
        "Себестоимость, ₸", max_digits=14, decimal_places=4, null=True, editable=False
    )

    history = HistoricalRecords(m2m_fields=[tags])

    class Meta:
        verbose_name = "Товар"
        verbose_name_plural = "Товары"
        ordering = ["name"]
        permissions = [("view_cost", "Видит себестоимость, прибыль и маржу")]
        constraints = [
            models.UniqueConstraint(fields=["name_key"], name="sales_product_name_uniq"),
            models.UniqueConstraint(
                fields=["common_barcode"],
                condition=~models.Q(common_barcode=""),
                name="sales_product_barcode_uniq",
            ),
            models.UniqueConstraint(
                fields=["article"],
                condition=~models.Q(article=""),
                name="sales_product_article_uniq",
            ),
        ]

    @property
    def last_snapshot(self):
        return self.snapshots.order_by("-created_at").first()

    def main_variant(self):
        item = self.items.select_related("variant__model").order_by("pk").first()
        return item.variant if item else None

    def activation_problems(self) -> list[str]:
        """Чего не хватает, чтобы товар стал активным (ТЗ п. 5.6, правило 8)."""
        from gallery.models import main_photo_for_product

        problems = []
        items = list(self.items.select_related("variant"))
        if not items:
            problems.append("в составе нет ни одного варианта")
        if self.kind == ProductKind.SINGLE and (len(items) != 1 or items[0].quantity != 1):
            problems.append("у поштучного товара в составе должен быть ровно один вариант × 1")
        if main_photo_for_product(self) is None:
            problems.append("нет главного фото (ни у товара, ни у варианта, ни у модели)")
        if not all([self.length_cm, self.width_cm, self.height_cm]):
            problems.append("не указаны габариты в упаковке")
        if not self.weight_g:
            problems.append("не указана масса в упаковке")
        if not self.article:
            problems.append("нет артикула: у категории модели не задан код для артикулов")
        return problems

    def save(self, *args, **kwargs):
        if not self.sku:
            self.sku = next_code("SKU")
        super().save(*args, **kwargs)

    def build_article(self) -> str:
        """
        Артикул по правилам: одна модель → основа модели + буква варианта (X, если цветов
        несколько), набор → ещё -N и количество; разные модели одной категории → код + X;
        разные категории с общим началом кода → общее начало + X; иначе XT.
        """
        import os

        items = list(self.items.select_related("variant__model__category"))
        if not items:
            return ""
        models_ = {item.variant.model for item in items}
        total = sum(item.quantity for item in items)
        tail = f"-N{total}" if self.kind == ProductKind.SET else ""
        if len(models_) == 1:
            model = items[0].variant.model
            if not model.article_base:
                return ""
            variants = {item.variant for item in items}
            letters = next(iter(variants)).article_letters if len(variants) == 1 else "X"
            return f"{model.article_base}{letters}{tail}"
        codes = [m.category.article_code if m.category_id else "" for m in models_]
        if not all(codes):
            return ""
        prefix = os.path.commonprefix(codes)
        mix = f"{prefix}X" if prefix else "XT"
        return f"{mix}{next_number(f'ART-{mix}'):04d}{tail}"

    def assign_article(self) -> str:
        """Присваивает артикул, если его ещё нет и его можно собрать. Возвращает артикул."""
        if self.article:
            return self.article
        article = self.build_article()
        if article and not Product.objects.filter(article=article).exclude(pk=self.pk).exists():
            self.article = article
            type(self).objects.filter(pk=self.pk).update(article=article)
        return self.article


class ProductItem(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey(
        Variant,
        verbose_name="Вариант",
        on_delete=models.PROTECT,
        related_name="product_items",
    )
    quantity = models.PositiveIntegerField("Кол-во", default=1, validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = "Вариант в товаре"
        verbose_name_plural = "Состав"
        constraints = [
            models.UniqueConstraint(fields=["product", "variant"], name="sales_productitem_uniq")
        ]

    def __str__(self):
        return f"{self.variant} × {self.quantity}"


class ProductPackaging(EnteredUnitMixin):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="packaging")
    material = models.ForeignKey(
        Material,
        verbose_name="Упаковка",
        on_delete=models.PROTECT,
        related_name="product_packaging",
        limit_choices_to={"category__kind": MaterialKind.PACKAGING},
    )
    quantity = positive_decimal_field(
        "Кол-во", default=Decimal(1), help_text="В единице материала или в единице ввода."
    )

    class Meta:
        verbose_name = "Упаковка товара"
        verbose_name_plural = "Упаковка товара"

    def __str__(self):
        return f"{self.material}: {self.quantity_display()}"


class ProductOperation(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="operations")
    operation_type = models.ForeignKey(
        OperationType,
        verbose_name="Операция",
        on_delete=models.PROTECT,
        limit_choices_to={"level": OperationLevel.PRODUCT},
    )
    minutes = models.DecimalField(
        "Время на 1 товар, мин",
        max_digits=8,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    rate = money_field("Ставка, ₸/ч", null=True, blank=True, help_text="Пусто — из типа операции.")

    class Meta:
        verbose_name = "Комплектация"
        verbose_name_plural = "Комплектация"

    def __str__(self):
        return str(self.operation_type)


class CostSnapshot(models.Model):
    """Зафиксированная себестоимость. Не редактируется и не удаляется (ТЗ п. 5.6)."""

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="snapshots")
    created_at = models.DateTimeField("Дата фиксации", auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Кто зафиксировал",
        null=True,
        on_delete=models.SET_NULL,
    )
    direct = models.DecimalField("Прямая себестоимость, ₸", max_digits=14, decimal_places=4)
    total = models.DecimalField("Полная себестоимость, ₸", max_digits=14, decimal_places=4)
    tree = models.JSONField("Разбивка")

    class Meta:
        verbose_name = "Снимок себестоимости"
        verbose_name_plural = "Снимки себестоимости"
        ordering = ["-created_at"]

    def __str__(self):
        from core.admin_utils import fmt_datetime

        return f"{self.product.sku} от {fmt_datetime(self.created_at)}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Зафиксированную себестоимость изменить нельзя.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Зафиксированную себестоимость удалить нельзя.")
