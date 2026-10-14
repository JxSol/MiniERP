from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.shortcuts import redirect
from django.urls import reverse
from simple_history.admin import SimpleHistoryAdmin
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import SiteSettings, Size, Tag


class ArchiveAdmin(ModelAdmin):
    """Общие настройки списков: архивные записи видны через фильтр «Активно»."""

    list_filter_submit = False
    warn_unsaved_form = True

    def get_list_filter(self, request):
        filters = list(super().get_list_filter(request))
        if "is_active" not in filters and hasattr(self.model, "is_active"):
            filters.insert(0, "is_active")
        return filters


@admin.register(SiteSettings)
class SiteSettingsAdmin(SimpleHistoryAdmin, ModelAdmin):
    """Одна запись: список сразу открывает форму, добавлять и удалять нельзя."""

    fieldsets = (
        (
            "Производство",
            {
                "fields": (
                    "electricity_tariff",
                    "labor_rate",
                    "default_scrap_percent",
                    "overhead_percent",
                )
            },
        ),
        (
            "Продажи",
            {
                "fields": (
                    "tax_percent",
                    "min_margin_percent",
                    "target_margin_percent",
                    "cost_growth_alert_percent",
                    "max_seller_discount_percent",
                )
            },
        ),
        ("Система", {"fields": ("site_url",)}),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        obj = SiteSettings.load()
        return redirect(reverse("admin:core_sitesettings_change", args=[obj.pk]))


@admin.register(Tag)
class TagAdmin(ArchiveAdmin):
    list_display = ("name", "is_active")
    search_fields = ("name",)
    fields = ("name", "is_active", "notes")


@admin.register(Size)
class SizeAdmin(ArchiveAdmin):
    list_display = ("name", "code", "order", "is_active")
    list_editable = ("order",)
    search_fields = ("name", "code")
    fields = ("name", "code", "order", "is_active", "notes")


# Пользователи и группы в оформлении unfold
admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass
