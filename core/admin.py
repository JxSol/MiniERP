from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.urls import reverse
from simple_history.admin import SimpleHistoryAdmin
from unfold.admin import ModelAdmin, TabularInline
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .forms import ActiveChoicesForm
from .models import SiteSettings, Size, Tag


class HistoryAdmin(SimpleHistoryAdmin):
    """
    История изменений. simple_history разрешает POST на странице версии любому,
    у кого есть право просмотра, — закрываем это: менять запись может только тот,
    у кого есть право на изменение.
    """

    def history_form_view(self, request, object_id, version_id, extra_context=None):
        if request.method == "POST" and not self.has_change_permission(request):
            raise PermissionDenied
        return super().history_form_view(request, object_id, version_id, extra_context)


class ArchiveAdmin(ModelAdmin):
    """
    Общие настройки: архивные записи видны через фильтр «Активно», не предлагаются
    в выпадающих списках и автокомплите, но у существующих записей остаются допустимыми.
    """

    form = ActiveChoicesForm
    list_filter_submit = False
    warn_unsaved_form = True

    def get_search_results(self, request, queryset, search_term):
        queryset, distinct = super().get_search_results(request, queryset, search_term)
        is_autocomplete = (
            request.resolver_match and request.resolver_match.url_name == "autocomplete"
        )
        if is_autocomplete and hasattr(self.model, "is_active"):
            queryset = queryset.filter(is_active=True)
        return queryset, distinct

    def get_list_filter(self, request):
        filters = list(super().get_list_filter(request))
        if "is_active" not in filters and hasattr(self.model, "is_active"):
            filters.insert(0, "is_active")
        return filters


@admin.register(SiteSettings)
class SiteSettingsAdmin(HistoryAdmin, ModelAdmin):
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
    list_display = ("name", "order", "is_active")
    list_editable = ("order",)
    search_fields = ("name",)
    fields = ("name", "order", "is_active", "notes")


class LineInline(TabularInline):
    """Табличная строка внутри карточки: архивные справочные записи не предлагаются."""

    form = ActiveChoicesForm
    extra = 0
    tab = True


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
