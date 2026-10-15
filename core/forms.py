"""
Формы, в которых архивные записи скрыты из выбора, но текущее значение
остаётся допустимым (ТЗ п. 5.7: архив прячет из списков, а не ломает записи).
"""

from django import forms
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
