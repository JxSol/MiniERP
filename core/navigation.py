"""Права для пунктов бокового меню: пункт виден, только если есть право на просмотр."""


def _can(perm):
    def check(request):
        return request.user.has_perm(perm)

    return check


view_product = _can("sales.view_product")
view_model = _can("production.view_productmodel")
view_variant = _can("production.view_variant")
view_part = _can("production.view_part")
view_material = _can("catalog.view_material")
view_color = _can("catalog.view_color")
view_equipment = _can("catalog.view_equipment")
view_operationtype = _can("catalog.view_operationtype")
view_materialcategory = _can("catalog.view_materialcategory")
view_productcategory = _can("production.view_productcategory")
view_tag = _can("core.view_tag")
view_size = _can("core.view_size")
view_settings = _can("core.view_sitesettings")
view_user = _can("auth.view_user")
view_group = _can("auth.view_group")
