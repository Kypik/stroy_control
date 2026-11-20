"""Пространство объекта: выбранный объект, фильтрация данных и выпадающих списков.

Объект хранится в session. Это не отдельные базы и не копии справочников —
все запросы к данным объекта строятся как обычные фильтры по связям моделей.
"""

from django.db.models import Q
from django.shortcuts import redirect
from django.urls import reverse

from .models import (
    ConstructionSite, DeliveryItem, DeliveryNote, DeviationResolution,
    DocumentationSection, EstimateLine, HiddenWorkAct, IncomingInspection,
    Room, SiteSigner, WorkType, WorkVolume,
)

SESSION_KEY = 'current_site_id'

_MISSING = object()


# ---------------------------------------------------------------------------
# Выбранный объект
# ---------------------------------------------------------------------------

def get_current_site(request):
    """Объект из session; удалённый объект автоматически забывается."""
    cached = getattr(request, '_current_site', _MISSING)
    if cached is not _MISSING:
        return cached

    pk = request.session.get(SESSION_KEY) if hasattr(request, 'session') else None
    site = ConstructionSite.objects.filter(pk=pk).first() if pk else None
    if pk and site is None:
        request.session.pop(SESSION_KEY, None)
    request._current_site = site
    return site


def set_current_site(request, site):
    if site is None:
        request.session.pop(SESSION_KEY, None)
    else:
        request.session[SESSION_KEY] = site.pk
    request._current_site = site


def legacy_site_from_request(request):
    """Объект для старого адреса: сначала ?site=, затем выбранный в session."""
    raw = request.GET.get('site')
    if raw:
        site = ConstructionSite.objects.filter(pk=raw).first()
        if site:
            return site
    return get_current_site(request)


def redirect_to_site(request, url_name, site, drop=('site',)):
    """Редирект на объектный адрес с сохранением остальных GET-параметров."""
    params = request.GET.copy()
    for key in drop:
        params.pop(key, None)
    url = reverse(url_name, args=[site.pk])
    query = params.urlencode()
    return redirect(f'{url}?{query}' if query else url)


# ---------------------------------------------------------------------------
# Какие модели относятся к объекту и как это выражается в ORM
# ---------------------------------------------------------------------------

_SITE_SCOPE = {
    DocumentationSection: lambda site: Q(site=site),
    Room: lambda site: Q(site=site),
    WorkType: lambda site: Q(section__site=site),
    EstimateLine: lambda site: Q(room__site=site),
    DeliveryNote: lambda site: Q(site=site),
    DeliveryItem: lambda site: Q(delivery_note__site=site),
    IncomingInspection: lambda site: Q(delivery_item__delivery_note__site=site),
    WorkVolume: lambda site: Q(room__site=site) | Q(work_type__section__site=site),
    DeviationResolution: lambda site: Q(estimate_line__room__site=site),
    HiddenWorkAct: lambda site: Q(section__site=site),
    SiteSigner: lambda site: Q(site=site),
}

# Сам справочник объектов не фильтруется по выбранному объекту — иначе в списке
# останется одна запись. Для полей-ссылок на ConstructionSite правило другое.
_LIST_SCOPED = {model: func for model, func in _SITE_SCOPE.items() if model is not ConstructionSite}


def model_is_site_scoped(model):
    return model in _LIST_SCOPED


def list_scope_q(model, site):
    builder = _LIST_SCOPED.get(model)
    return builder(site) if builder else None


def related_scope_q(model, site):
    """Ограничение для выпадающего списка: ссылка на объект — только этот объект,
    ссылка на объектную запись — записи этого объекта, глобальные справочники не трогаем."""
    if model is ConstructionSite:
        return Q(pk=site.pk)
    builder = _SITE_SCOPE.get(model)
    return builder(site) if builder else None


def filter_queryset(qs, model, site, for_list=True):
    if site is None:
        return qs
    condition = list_scope_q(model, site) if for_list else related_scope_q(model, site)
    return qs.filter(condition) if condition is not None else qs


def instance_site(obj):
    """Объект, к которому относится запись (для карточек и редактирования)."""
    if obj is None:
        return None
    if isinstance(obj, ConstructionSite):
        return obj
    if isinstance(obj, (DocumentationSection, Room, DeliveryNote, SiteSigner)):
        return obj.site
    if isinstance(obj, WorkType):
        return obj.section.site
    if isinstance(obj, EstimateLine):
        return obj.room.site
    if isinstance(obj, DeliveryItem):
        return obj.delivery_note.site
    if isinstance(obj, IncomingInspection):
        return obj.delivery_item.delivery_note.site
    if isinstance(obj, WorkVolume):
        return obj.room.site if obj.room_id else obj.work_type.section.site
    if isinstance(obj, DeviationResolution):
        return obj.estimate_line.room.site
    if isinstance(obj, HiddenWorkAct):
        return obj.section.site
    return None


def restrict_form_to_site(form, site):
    """Сужает выпадающие списки формы до данных выбранного объекта.
    Глобальные справочники (контрагенты, участники, нормативы, материалы) не трогаем."""
    if site is None:
        return form
    for field in form.fields.values():
        queryset = getattr(field, 'queryset', None)
        if queryset is None:
            continue
        condition = related_scope_q(queryset.model, site)
        if condition is not None:
            field.queryset = queryset.filter(condition)
    return form


# ---------------------------------------------------------------------------
# Контекст шаблонов
# ---------------------------------------------------------------------------

def site_context(request):
    return {
        'current_site': get_current_site(request),
        'header_sites': ConstructionSite.objects.order_by('name'),
    }
