"""
Формы, в которых архивные записи скрыты из выбора, но текущее значение
остаётся допустимым (ТЗ п. 5.7: архив прячет из списков, а не ломает записи).
"""

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q


def _has_is_active(model) -> bool:
    return any(f.name == "is_active" for f in model._meta.fields)


class ActiveChoicesForm(forms.ModelForm):
    """Ограничивает ModelChoiceField/ModelMultipleChoiceField активными записями + текущими."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = self.instance
        for name, field in self.fields.items():
            if not isinstance(field, forms.ModelChoiceField):
                continue
            model = field.queryset.model
            if not _has_is_active(model):
                continue
            condition = Q(is_active=True)
            if instance.pk:
                try:
                    model_field = instance._meta.get_field(name)
                except Exception:  # noqa: BLE001 — поле формы без поля модели
                    model_field = None
                if model_field is not None and model_field.many_to_many:
                    condition |= Q(pk__in=getattr(instance, name).values("pk"))
                elif model_field is not None and getattr(instance, f"{name}_id", None):
                    condition |= Q(pk=getattr(instance, f"{name}_id"))
            field.queryset = field.queryset.filter(condition).distinct()


class QuantityUnitFormMixin:
    """
    Количество можно ввести в единице расхода материала или в альтернативной (м², пог. м).
    В базе хранится единица расхода, а выбранная единица запоминается в entered_unit,
    поэтому при редактировании видно то, что вводили.
    """

    quantity_field = "quantity"
    unit_field = "entered_unit"
    material_field = "material"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.unit_field in self.fields:
            self.fields[self.unit_field].choices = [
                ("", "ед. материала"),
                *[c for c in self.fields[self.unit_field].choices if c[0]],
            ]
        instance = self.instance
        unit = getattr(instance, self.unit_field, "")
        if instance.pk and unit and self.quantity_field in self.fields:
            material = getattr(instance, self.material_field, None)
            if material is not None:
                self.initial[self.quantity_field] = material.from_base(
                    getattr(instance, self.quantity_field), unit
                )

    def clean(self):
        data = super().clean()
        material = data.get(self.material_field)
        unit = data.get(self.unit_field) or ""
        quantity = data.get(self.quantity_field)
        if unit and material is None:
            self.add_error(self.unit_field, "Единица ввода указывается только для материала.")
        elif material is not None and quantity is not None:
            try:
                data[self.quantity_field] = material.to_base(quantity, unit)
            except ValidationError as error:
                self.add_error(self.unit_field, error)
        return data
