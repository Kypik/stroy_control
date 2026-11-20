"""
core/views.py

Интерфейс инженера:
  /                       — выбранный объект или список объектов
  /sites/<id>/            — обзор объекта
  /sites/<id>/acts/       — акты объекта
  /acts/new/              — старый адрес нового акта (перенаправляется в объект)
  /acts/<id>/             — карточка акта (всё редактируется на одной странице)
  /acts/<id>/excel/       — сформировать и скачать Excel
  /acts/<id>/copy/        — создать новый акт на основе этого
  /acts/<id>/delete/      — удалить акт
  /ref/                   — справочники
  /ref/<slug>/            — список записей справочника
  /ref/<slug>/new/        — добавить запись
  /ref/<slug>/<id>/       — изменить запись
  /ref/<slug>/<id>/delete/
"""

import logging
import os
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import FieldDoesNotExist
from django.core.paginator import Paginator
from django.db import models, transaction
from django.db.models import Count, ProtectedError, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .form_layouts import grouped_fields
from .forms import field_label, group_choices, make_form, make_inline_formset
from .models import (
    ActAttachment, ActMaterial, ActSigner, ConstructionSite, DeliveryItem,
    DeliveryNote, DocumentationSection, HiddenWorkAct, Material, Person,
    SiteSigner, WorkType, WorkVolume,
)
from .act_workflow import is_allowed, status_label, transition_buttons
from .events import log_event
from .registry import get_entries, get_entry
from .services import (
    build_header_context, header_for_act, header_overrides_from_post,
    header_row_groups, header_rows, header_snapshot_from_post, header_summary,
    header_to_snapshot,
)
from .site_context import (
    filter_queryset, get_current_site, instance_site, legacy_site_from_request,
    model_is_site_scoped, redirect_to_site, related_scope_q, restrict_form_to_site,
    set_current_site,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Вспомогательное
# ---------------------------------------------------------------------------

# Колонки, которых нет в модели, но которые полезны в списке (считаются на лету).
VIRTUAL_COLUMNS = {'materials': 'Материалы'}


def _display(obj, name):
    """Значение поля для показа в таблице: выбор — подписью, дата — по-русски, да/нет."""
    try:
        field = obj._meta.get_field(name)
    except FieldDoesNotExist:
        if name == 'materials' and hasattr(obj, 'materials_label'):
            label = obj.materials_label()
            if not label and getattr(obj, 'product_text', ''):
                # материал ещё не привязан — показываем хотя бы продукцию из документа
                product = ' '.join(obj.product_text.split())
                label = product[:80] + ('…' if len(product) > 80 else '')
            return label
        return ''
    if field.choices:
        return getattr(obj, f'get_{name}_display')()
    value = getattr(obj, name, '')
    if value is None or value == '':
        return ''
    if isinstance(value, bool):
        return 'да' if value else 'нет'
    if isinstance(value, date):
        return value.strftime('%d.%m.%Y')
    if isinstance(field, models.FileField):
        return os.path.basename(value.name) if value else ''
    return str(value)


def _safe_next(request):
    nxt = request.POST.get('next') or request.GET.get('next')
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return None


def _querystring_without_page(request):
    params = request.GET.copy()
    params.pop('page', None)
    return params.urlencode()


_RELATED_SORT_CANDIDATES = ('name', 'short_name', 'last_name', 'designation', 'document_number',
                            'number', 'act_number', 'explication_name', 'text')

_SEARCH_CANDIDATES = ('name', 'short_name', 'full_name', 'last_name', 'first_name', 'designation',
                      'document_number', 'number', 'act_number', 'explication_name', 'text',
                      'original_text', 'position', 'code', 'project_code', 'estimate_code')


def _search_condition(model, q, extra_paths=()):
    """Простой поиск: свои текстовые поля + текстовые поля связанных записей
    (производитель, поставщик, материал и т.п. ищутся по названию).
    extra_paths — дополнительные пути ORM из registry (обратные связи)."""
    cond = Q()
    for f in model._meta.fields:
        if isinstance(f, (models.CharField, models.TextField)) and not f.choices:
            cond |= Q(**{f'{f.name}__icontains': q})
        elif isinstance(f, models.ForeignKey):
            related = {rf.name for rf in f.related_model._meta.fields}
            for cand in _SEARCH_CANDIDATES:
                if cand in related:
                    cond |= Q(**{f'{f.name}__{cand}__icontains': q})
    for path in extra_paths:
        cond |= Q(**{f'{path}__icontains': q})
    return cond


def _related_sort_field(model):
    """Поле связанной модели, по которому её осмысленно сортировать в списках."""
    names = {f.name for f in model._meta.fields}
    return next((n for n in _RELATED_SORT_CANDIDATES if n in names), 'pk')


def _apply_sort(qs, sort, mapping, default):
    """Сортировка по GET-параметру sort. mapping: ключ колонки -> поле ORM."""
    key = sort.lstrip('-')
    if key not in mapping:
        return qs.order_by(*default)
    prefix = '-' if sort.startswith('-') else ''
    return qs.order_by(f'{prefix}{mapping[key]}', 'pk')


def _sort_headers(request, keys, labels, current, sortable=None):
    """Заголовки таблицы со ссылками: повторный клик меняет направление сортировки."""
    params = request.GET.copy()
    params.pop('page', None)
    active = current.lstrip('-')
    ascending = bool(current) and not current.startswith('-')
    headers = []
    for key, label in zip(keys, labels):
        if sortable is not None and key not in sortable:
            headers.append({'label': label, 'url': '', 'arrow': ''})
            continue
        params['sort'] = f'-{key}' if key == active and ascending else key
        arrow = ''
        if key == active:
            arrow = '▲' if ascending else '▼'
        headers.append({'label': label, 'url': f'?{params.urlencode()}', 'arrow': arrow})
    return headers


def _delivery_item_label(item):
    note = item.delivery_note
    what = item.material or item.original_text
    qty = item.quantity if item.quantity is not None else ''
    return f"{what} — накл. №{note.number} от {note.delivery_date:%d.%m.%Y}, {qty} {item.unit}"


NO_CATEGORY = 'Без категории'


def _material_catalog(act):
    """Материалы по категориям и их поставки на объект. Без поставки выбирается сам материал.
    Поставка по умолчанию — последняя, пришедшая не позже начала работ по акту.
    Отдельно возвращаются недавние поставки — типовой случай «только что привезли»."""
    site = act.section.site
    first_work = WorkVolume.objects.filter(act=act).order_by('performance_date') \
        .values_list('performance_date', flat=True).first()

    items_by_material = {}
    items = list(
        DeliveryItem.objects.filter(delivery_note__site=site, material__isnull=False)
        .select_related('delivery_note', 'material')
        .order_by('-delivery_note__delivery_date', '-id')
    )
    for item in items:
        items_by_material.setdefault(item.material_id, []).append(item)

    act_materials = ActMaterial.objects.filter(act=act)
    already = set(act_materials.values_list('delivery_item__material_id', flat=True))
    already.update(act_materials.values_list('material_id', flat=True))

    recent = []
    for item in items[:10]:
        material = item.material
        category = (getattr(material, 'category', '') or '').strip() or NO_CATEGORY
        note = item.delivery_note
        recent.append({
            'item_id': item.id,
            'material_id': material.id,
            'name': material.name,
            'unit': item.unit or material.unit or '',
            'quantity': item.quantity if item.quantity is not None else '',
            'note_number': note.number,
            'delivery_date': note.delivery_date.strftime('%d.%m.%Y') if note.delivery_date else '',
            'in_act': material.id in already,
            'search': ' '.join(filter(None, [
                material.name, category, material.unit,
                f'№{note.number}' if note.number else '',
                note.delivery_date.strftime('%d.%m.%Y') if note.delivery_date else '',
            ])).lower(),
        })

    groups = {}
    materials = Material.objects.exclude(status='archived').order_by('name')
    for m in materials:
        m_items = items_by_material.get(m.id, [])
        default = None
        if m_items:
            fitting = [i for i in m_items if not first_work or i.delivery_note.delivery_date <= first_work]
            default = (fitting or m_items)[0]
        category = (getattr(m, 'category', '') or '').strip() or NO_CATEGORY
        groups.setdefault(category, []).append({
            'id': m.id,
            'name': m.name,
            'unit': getattr(m, 'unit', ''),
            'in_act': m.id in already,
            'default_item': default.id if default else None,
            'deliveries': len(m_items),
            'last_delivery': f"{m_items[0].delivery_note.delivery_date:%d.%m.%Y}" if m_items else '',
            'search': ' '.join(filter(None, [
                m.name, category, getattr(m, 'unit', ''),
                f'{m_items[0].delivery_note.delivery_date:%d.%m.%Y}' if m_items else '',
            ])).lower(),
        })

    ordered = sorted(groups, key=lambda c: (c == NO_CATEGORY, c.lower()))
    return {
        'recent': recent,
        'groups': [{'category': c, 'materials': groups[c]} for c in ordered],
    }


# ---------------------------------------------------------------------------
# Акты
# ---------------------------------------------------------------------------

ACT_SORTS = {
    'number': 'act_number',
    'date': 'drawn_up_date',
    'section': 'section__name',
    'site': 'section__site__name',
    'status': 'status',
}


@login_required
def act_list(request, site_pk=None):
    if site_pk is None:
        site = legacy_site_from_request(request)
        if site:
            return redirect_to_site(request, 'site_acts', site)
        return redirect('site_list')

    site = get_object_or_404(ConstructionSite, pk=site_pk)
    set_current_site(request, site)
    acts = HiddenWorkAct.objects.filter(section__site=site).select_related(
        'section', 'section__site')

    status = request.GET.get('status', '')
    if status:
        acts = acts.filter(status=status)
    q = request.GET.get('q', '').strip()
    if q:
        acts = acts.filter(
            Q(act_number__icontains=q) | Q(section__name__icontains=q)
            | Q(section__site__name__icontains=q)
            | Q(workvolume__work_type__name__icontains=q)
            | Q(actmaterial__delivery_item__material__name__icontains=q)
            | Q(actmaterial__delivery_item__original_text__icontains=q)
            | Q(actmaterial__material__name__icontains=q)
        ).distinct()

    sort = request.GET.get('sort', '')
    acts = _apply_sort(acts, sort, ACT_SORTS, ('-drawn_up_date', '-id'))

    page = Paginator(acts, 50).get_page(request.GET.get('page'))
    return render(request, 'core/act_list.html', {
        'page': page,
        'statuses': HiddenWorkAct._meta.get_field('status').choices,
        'status': status,
        'sites': ConstructionSite.objects.order_by('name'),
        'site': site,
        'locked_site': site,
        'q': q,
        'headers': _sort_headers(
            request, list(ACT_SORTS), ['№', 'Дата', 'Раздел', 'Объект', 'Статус'], sort),
        'qs': _querystring_without_page(request),
    })


def _header_with_overrides(header, data):
    """Шапка с правками из отправленной формы — чтобы блок показывал введённое."""
    overrides = header_overrides_from_post(data) if data is not None else None
    return {**header, **overrides} if overrides else header


def _next_act_number(section):
    numbers = []
    for value in HiddenWorkAct.objects.filter(section=section).values_list('act_number', flat=True):
        match = re.match(r'\s*(\d+)', value or '')
        if match:
            numbers.append(int(match.group(1)))
    return str(max(numbers, default=0) + 1)


@login_required
def act_create(request, site_pk=None):
    if site_pk is None:
        site = get_current_site(request)
        if site:
            return redirect('site_act_create', site_pk=site.pk)
        return redirect('site_list')

    site = get_object_or_404(ConstructionSite, pk=site_pk)
    set_current_site(request, site)
    ActForm = make_form(HiddenWorkAct, exclude=['corrected_file', 'header_snapshot'])
    form = ActForm(request.POST or None, initial={'drawn_up_date': date.today()})
    form.fields['section'].queryset = DocumentationSection.objects.filter(site=site) \
        .order_by('order_number', 'name')
    # Один раздел у объекта — подставляем его сразу, чтобы не выбирать из одного варианта.
    if request.method != 'POST' and form.fields['section'].queryset.count() == 1:
        form.initial['section'] = form.fields['section'].queryset.first().pk
    # Черновик собирается кусками: обязательны только раздел и дата (дата предзаполнена)
    for name, field in form.fields.items():
        if name not in ('section', 'drawn_up_date', 'status'):
            field.required = False
    form.fields['act_number'].widget.attrs['placeholder'] = 'пусто — номер подставится сам'
    for name in ('next_work_type', 'next_work_override'):
        form.fields.pop(name, None)

    site_header = build_header_context(site.pk)

    if request.method == 'POST' and form.is_valid():
        act = form.save(commit=False)
        if act.section.site_id != site.pk:
            raise Http404('Раздел относится к другому объекту')
        if not act.act_number:
            act.act_number = _next_act_number(act.section)
        # шапка фиксируется снимком: данные объекта + правки из скрытого блока
        act.header_snapshot = (header_snapshot_from_post(site_header, request.POST)
                               or header_to_snapshot(site_header))
        act.save()
        form.save_m2m()
        # подписанты по умолчанию из карточки объекта — копия-снимок, обратной синхронизации нет
        for ss in SiteSigner.objects.filter(site=act.section.site):
            ActSigner.objects.create(act=act, person=ss.person, role_in_act=ss.role_in_act)
        messages.success(request, f'Акт №{act.act_number} создан ({act.section.site.name}, '
                                  f'раздел «{act.section.name}»). Заполните работы, материалы и подписантов.')
        return redirect('act_edit', pk=act.pk)

    header = _header_with_overrides(site_header, request.POST if request.method == 'POST' else None)
    return render(request, 'core/act_create.html', {
        'form': form, 'site': site, 'locked_site': site,
        'header_rows': header_rows(header),
        'header_groups': header_row_groups(header),
        'header_summary': header_summary(header),
        'section_count': form.fields['section'].queryset.count(),
    })


@login_required
def site_act_from_volumes(request, site_pk):
    """Акт из готовых замеров: выбираем раздел и свободные объёмы этого раздела."""
    site = get_object_or_404(ConstructionSite, pk=site_pk)
    set_current_site(request, site)

    sections = DocumentationSection.objects.filter(site=site).annotate(
        free_count=Count('worktype__workvolume', distinct=True,
                         filter=Q(worktype__workvolume__act__isnull=True)),
    ).order_by('order_number', 'name')

    section = None
    section_id = request.POST.get('section') if request.method == 'POST' \
        else request.GET.get('section')
    if section_id:
        try:
            section = get_object_or_404(sections, pk=int(section_id))
        except (TypeError, ValueError):
            raise Http404('Раздел не найден')

    free_volumes = WorkVolume.objects.none()
    if section:
        free_volumes = WorkVolume.objects.filter(
            act__isnull=True, work_type__section=section,
        ).select_related('work_type', 'room').order_by('performance_date', 'pk')

    selected_ids = []

    def page():
        return render(request, 'core/act_from_volumes.html', {
            'site': site, 'locked_site': site,
            'sections': sections, 'section': section,
            'free_volumes': free_volumes, 'selected_ids': selected_ids,
        })

    if request.method != 'POST':
        return page()

    if section is None:
        messages.error(request, 'Выберите раздел, замеры которого войдут в акт.')
        return page()

    raw_ids = request.POST.getlist('volume')
    if not raw_ids:
        messages.error(request, 'Отметьте хотя бы один замер — акт создаётся из выбранных замеров.')
        return page()

    try:
        selected_ids = sorted({int(value) for value in raw_ids})
    except (TypeError, ValueError):
        messages.error(request, 'Некорректный список замеров. Обновите страницу и выберите заново.')
        return page()

    error = None
    act = None
    with transaction.atomic():
        # Блокируем раздел: номер акта выдаётся по уже существующим актам раздела.
        section = DocumentationSection.objects.select_for_update().get(pk=section.pk)
        volumes = list(WorkVolume.objects.select_for_update().filter(
            pk__in=selected_ids, act__isnull=True, work_type__section=section,
        ).values_list('pk', flat=True))
        if len(volumes) != len(selected_ids):
            error = ('Часть отмеченных замеров уже привязана к другому акту или относится '
                     'к другому разделу. Обновите страницу и выберите заново.')
        else:
            act = HiddenWorkAct.objects.create(
                act_number=_next_act_number(section),
                drawn_up_date=date.today(),
                section=section,
                status=HiddenWorkAct.Status.DRAFT,
                # шапка фиксируется снимком готовым хелпером — как при обычном создании акта
                header_snapshot=header_to_snapshot(build_header_context(site.pk)),
            )
            # подписанты по умолчанию из карточки объекта — копия-снимок
            for ss in SiteSigner.objects.filter(site=site):
                ActSigner.objects.create(act=act, person=ss.person, role_in_act=ss.role_in_act)
            WorkVolume.objects.filter(pk__in=volumes).update(act=act)

    if error:
        messages.error(request, error)
        return page()

    log_event(request, 'Создан акт из замеров', act, f'замеров {len(volumes)}')
    messages.success(request, f'Акт №{act.act_number} создан из замеров: {len(volumes)}. '
                              f'Объект «{site.name}», раздел «{section.name}».')
    return redirect('act_edit', pk=act.pk)


def _restrict(formset, apply):
    """Подменяет класс формы в наборе строк так, чтобы apply(form) срабатывал для каждой строки,
    включая заготовку новой строки (empty_form создаётся заново при каждом обращении)."""
    base = formset.form

    class Restricted(base):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            apply(self)

    Restricted.__name__ = base.__name__
    formset.form = Restricted


def _build_act_formsets(act, data=None):
    site = act.section.site

    def extra_for(model):
        return 0 if model.objects.filter(act=act).exists() else 1

    VolumeFS = make_inline_formset(
        HiddenWorkAct, WorkVolume, 'act', exclude=['room'], extra=extra_for(WorkVolume))
    MaterialFS = make_inline_formset(HiddenWorkAct, ActMaterial, 'act', extra=extra_for(ActMaterial))
    SignerFS = make_inline_formset(HiddenWorkAct, ActSigner, 'act', extra=extra_for(ActSigner))
    AttachmentFS = make_inline_formset(HiddenWorkAct, ActAttachment, 'act', extra=extra_for(ActAttachment))

    volumes = VolumeFS(data, instance=act, prefix='vol')
    materials = MaterialFS(data, instance=act, prefix='mat')
    signers = SignerFS(data, instance=act, prefix='sig')
    attachments = AttachmentFS(data, instance=act, prefix='att')

    # Ограничиваем выпадающие списки тем, что относится к этому объекту и разделу.
    # Ограничения встраиваются в класс формы, поэтому действуют и на строки,
    # добавленные кнопкой «+ Добавить строку».
    work_types = WorkType.objects.filter(section=act.section)
    items = DeliveryItem.objects.filter(delivery_note__site=site).select_related(
        'delivery_note', 'material').order_by('-delivery_note__delivery_date')
    material_options = Material.objects.exclude(status='archived').order_by('name')
    persons = Person.objects.select_related('organization').order_by('last_name')

    def restrict_volume(f):
        f.fields['work_type'].queryset = work_types
        # строка-черновик может быть заполнена частично — обязателен только вид работ
        for name in ('location', 'performed_by', 'performance_date', 'end_date', 'actual_volume', 'unit'):
            f.fields[name].required = False

    def restrict_material(f):
        field = f.fields['delivery_item']
        field.queryset = items
        field.label_from_instance = _delivery_item_label
        group_choices(field, lambda i: i.material.category if i.material else '')
        f.fields['material'].queryset = material_options
        f.fields['written_off_volume'].required = False

    def restrict_signer(f):
        f.fields['person'].queryset = persons

    _restrict(volumes, restrict_volume)
    _restrict(materials, restrict_material)
    _restrict(signers, restrict_signer)

    return volumes, materials, signers, attachments


# Секции карточки акта: предупреждения ведут прямо к тому месту, которое нужно исправить.
WARNING_SECTIONS = {
    'panel-req': 'req',
    'panel-vol': 'vol',
    'panel-mat': 'mat',
    'panel-sig': 'sig',
    'panel-att': 'att',
    'panel-docs': 'docs',
}


def act_warning_items(act):
    """Чего в акте не хватает — с привязкой к секции, строке формсета и полю.
    Excel при этом формируется всегда: недостающее видно и в самом файле
    (пустые места и строки «⚠ …»)."""
    items = []

    def add(text, section, pk=None, field='', severity='warn'):
        items.append({
            'text': text,
            'section': section,
            'section_key': WARNING_SECTIONS.get(section, ''),
            'pk': pk,
            'field': field,
            'severity': severity,
        })

    volumes = list(WorkVolume.objects.filter(act=act).select_related('work_type', 'room'))
    if not volumes:
        add('Нет ни одного замера: описание работ и даты начала/окончания будут пустыми.',
            'panel-vol')
    for i, v in enumerate(volumes, start=1):
        missing = []
        if not v.work_type_id:
            missing.append(('work_type', 'вид работ'))
        if not v.location.strip():
            missing.append(('location', 'место выполнения'))
        if v.actual_volume is None:
            missing.append(('actual_volume', 'объём'))
        if not v.performed_by_id:
            missing.append(('performed_by', 'кто выполнял'))
        if not v.performance_date:
            missing.append(('performance_date', 'дату начала'))
        if missing:
            what = ' — '.join(filter(None, [
                str(v.work_type) if v.work_type_id else '',
                v.location.strip() or str(v.room or ''),
            ]))
            label = f'Замер №{i}' + (f' ({what})' if what else '')
            add(f'{label}: не заполнено — {", ".join(text for _, text in missing)}.',
                'panel-vol', pk=v.pk, field=missing[0][0])

    signers = list(ActSigner.objects.filter(act=act).select_related('person'))
    if not signers:
        add('Не указан ни один подписант.', 'panel-sig')
    roles_seen = {}
    for s in signers:
        if s.role_in_act in roles_seen:
            add(f'Роль «{s.get_role_in_act_display()}» указана дважды — в файл попадёт только один человек.',
                'panel-sig', pk=s.pk, field='role_in_act')
        roles_seen[s.role_in_act] = s
        end = getattr(s.person, 'authority_end_date', None)
        if end and act.drawn_up_date and end < act.drawn_up_date:
            add(f'{s.person}: полномочия закончились {end:%d.%m.%Y}, а акт датирован {act.drawn_up_date:%d.%m.%Y}.',
                'panel-sig', pk=s.pk, field='person')

    act_materials = list(ActMaterial.objects.filter(act=act).select_related(
        'delivery_item__delivery_note', 'delivery_item__material', 'material'))
    if not act_materials:
        add('Не выбран ни один материал: раздел 3 и список приложений будут пустыми.',
            'panel-mat')
    work_dates = [v.performance_date for v in volumes if v.performance_date]
    first_work = min(work_dates) if work_dates else None
    for am in act_materials:
        item = am.delivery_item
        what = am.material or ((item.material or item.original_text) if item else 'Материал')
        if am.written_off_volume is None:
            add(f'{what}: не указано, сколько списано в акт.',
                'panel-mat', pk=am.pk, field='written_off_volume')
        if item is None:
            continue
        if item.material is None:
            add(f'Позиция «{item.original_text}» не сопоставлена с материалом из справочника.',
                'panel-mat', pk=am.pk, field='delivery_item')
        delivered = item.delivery_note.delivery_date
        if first_work and delivered and delivered > first_work:
            add(f'{what}: поставлен {delivered:%d.%m.%Y}, '
                f'а работы начаты {first_work:%d.%m.%Y}.',
                'panel-mat', pk=am.pk, field='delivery_item')

    if not act.next_work_type and not act.next_work_override:
        add('Не указаны последующие работы — п. 7 акта будет пустым.',
            'panel-req', field='next_work_type')

    try:
        from .services import blocking_problems, build_act_context
        from .act_export import act_needs_registry
        context = build_act_context(act.pk)
        for problem in blocking_problems(context):
            add(f'{problem}. Excel можно сформировать как есть — '
                f'место в файле будет отмечено «⚠».', 'panel-mat')
        if not context['has_registry'] and act_needs_registry(act):
            add('Акт не помещается на двух страницах: при сохранении '
                'будет автоматически сформирован реестр к АОСР.', 'panel-docs')
    except Exception:
        logger.exception('Не удалось проверить документы акта %s', act.pk)
        add('Не удалось проверить документы о качестве. Проверьте их вручную.', 'panel-mat')

    return items


def act_warnings(act):
    """Совместимая обёртка: список текстов без служебных полей."""
    return [item['text'] for item in act_warning_items(act)]


def warning_counts(items):
    """Сколько замечаний приходится на каждую секцию — для бейджей и авто-раскрытия."""
    counts = {}
    for item in items:
        key = item['section_key']
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def act_blocking(act):
    """Проблемы с документами о качестве (не блокируют формирование Excel —
    показываются предупреждениями и знаками «⚠» в файле)."""
    try:
        from .services import blocking_problems, build_act_context
        return blocking_problems(build_act_context(act.pk))
    except Exception:
        logger.exception('Не удалось собрать данные акта %s', act.pk)
        return ['Не удалось проверить документы о качестве акта.']


@login_required
def act_edit(request, pk):
    act = get_object_or_404(HiddenWorkAct.objects.select_related('section', 'section__site'), pk=pk)
    original_status = act.status
    site = act.section.site
    set_current_site(request, site)

    ActForm = make_form(HiddenWorkAct, exclude=['corrected_file', 'header_snapshot'])
    data = request.POST if request.method == 'POST' else None
    form = ActForm(data, instance=act)
    form.fields['section'].queryset = DocumentationSection.objects.filter(site=site) \
        .order_by('order_number', 'name')
    if 'next_work_type' in form.fields:
        form.fields['next_work_type'].queryset = WorkType.objects.filter(section__site=site)
    # акт дополняется постепенно: обязательны только раздел, дата и статус
    for name, field in form.fields.items():
        if name not in ('section', 'drawn_up_date', 'status'):
            field.required = False
    # Статусом управляют кнопки workflow, а не свободный выпадающий список.
    form.fields['status'].widget = forms.HiddenInput()
    volumes, materials, signers, attachments = _build_act_formsets(act, data)
    formsets = [volumes, materials, signers, attachments]

    if request.method == 'POST':
        transition = (request.POST.get('transition') or '').strip()
        if transition and not is_allowed(act.status, transition):
            form.add_error(None, 'Этот переход статуса недоступен из текущего состояния.')
        if form.is_valid() and all(fs.is_valid() for fs in formsets):
            with transaction.atomic():
                act = form.save()

                # Шапка: правки скрытого блока идут только в снимок этого акта.
                # «Вернуть как у объекта» пересобирает снимок из карточки объекта.
                if request.POST.get('header_action') == 'reset':
                    act.header_snapshot = header_to_snapshot(build_header_context(site.pk))
                    act.save(update_fields=['header_snapshot'])
                else:
                    snapshot = header_snapshot_from_post(header_for_act(act), request.POST)
                    if snapshot is not None:
                        act.header_snapshot = snapshot
                        act.save(update_fields=['header_snapshot'])

                # Замеры: «убрать» = отвязать от акта, а не удалить сам замер
                for obj in volumes.save(commit=False):
                    obj.act = act
                    obj.save()
                for obj in volumes.deleted_objects:
                    obj.act = None
                    obj.save()

                for fs in (materials, signers, attachments):
                    for obj in fs.save(commit=False):
                        obj.act = act
                        obj.save()
                    for obj in fs.deleted_objects:
                        obj.delete()

                attach_ids = request.POST.getlist('attach_volumes')
                if attach_ids:
                    WorkVolume.objects.filter(id__in=attach_ids, act__isnull=True).update(act=act)

                # Скрытое поле статуса не доверяем: меняем его только допустимым переходом.
                new_status = transition or original_status
                if act.status != new_status:
                    act.status = new_status
                    act.save(update_fields=['status'])
                if transition:
                    log_event(request, 'Изменён статус акта', act,
                              f'{status_label(original_status)} → {status_label(transition)}')

            try:
                from .registry_export import sync_act_registry
                sync_act_registry(act)
            except Exception:
                logger.exception('Не удалось обновить реестр акта %s', act.pk)
                messages.warning(request, 'Акт сохранён, но реестр не обновлён. Сформируйте его вручную.')

            if request.POST.get('action') == 'excel':
                return redirect('act_excel', pk=act.pk)
            messages.success(request, 'Сохранено. Статус: ' + status_label(act.status) + '.')
            return redirect('act_edit', pk=act.pk)
        messages.error(request, 'Не сохранено: проверьте поля, отмеченные красным.')

    free_volumes = WorkVolume.objects.filter(
        act__isnull=True, work_type__section=act.section,
    ).select_related('work_type').order_by('performance_date')

    header = _header_with_overrides(
        header_for_act(act), request.POST if request.method == 'POST' else None)
    warnings = act_warning_items(act)

    return render(request, 'core/act_edit.html', {
        'act': act,
        'form': form,
        'volumes': volumes,
        'materials': materials,
        'signers': signers,
        'attachments': attachments,
        'free_volumes': free_volumes,
        'warnings': [w['text'] for w in warnings],
        'warning_items': warnings,
        'warning_counts': warning_counts(warnings),
        'transitions': transition_buttons(act),
        'corrected_name': os.path.basename(act.corrected_file.name) if act.corrected_file else '',
        'header_rows': header_rows(header),
        'header_groups': header_row_groups(header),
        'header_summary': header_summary(header),
        'header_is_snapshot': bool(act.header_snapshot),
    })


def _select_options(form):
    """Варианты списков для обновления на странице. Группа: [название, [[значение, подпись], ...]]."""
    result = {}
    for name, field in form.fields.items():
        if hasattr(field, 'queryset'):
            options = []
            for value, label in field.choices:
                if isinstance(label, (list, tuple)):
                    options.append([str(value), [[str(v), str(l)] for v, l in label]])
                else:
                    options.append([str(value), str(label)])
            result[name] = options
    return result


@login_required
def act_material_picker(request, pk):
    """Содержимое окна «Выбрать материалы» — подгружается при каждом открытии, поэтому всегда свежее."""
    act = get_object_or_404(HiddenWorkAct.objects.select_related('section', 'section__site'), pk=pk)
    return render(request, 'core/_material_picker.html', {'catalog': _material_catalog(act), 'act': act})


@login_required
def act_options(request, pk):
    """Свежие варианты для всех выпадающих списков карточки акта.
    Нужны, когда в отдельном окне добавили участника, материал и т.п."""
    act = get_object_or_404(HiddenWorkAct.objects.select_related('section', 'section__site'), pk=pk)
    form = make_form(HiddenWorkAct, exclude=['corrected_file', 'header_snapshot'])(instance=act)
    form.fields['section'].queryset = DocumentationSection.objects.filter(
        site=act.section.site).order_by('order_number', 'name')
    if 'next_work_type' in form.fields:
        form.fields['next_work_type'].queryset = WorkType.objects.filter(section__site=act.section.site)
    options = _select_options(form)
    for fs in _build_act_formsets(act):
        for name, choices in _select_options(fs.empty_form).items():
            options[f'{fs.prefix}:{name}'] = choices
    return JsonResponse(options)


@login_required
def act_excel(request, pk):
    act = get_object_or_404(HiddenWorkAct, pk=pk)
    try:
        from .act_export import generate_act_excel
        path = generate_act_excel(act.pk)
    except Exception:
        logger.exception('Не удалось сформировать Excel акта %s', pk)
        messages.error(request, 'Excel не сформирован. Проверьте данные акта и повторите попытку.')
        return redirect('act_edit', pk=act.pk)

    # Проблемы с документами о качестве не мешают выпуску: Excel уже сформирован,
    # пользователь получает их предупреждением, а в файле такие места отмечены «⚠».
    try:
        from .services import blocking_problems, build_act_context
        problems = blocking_problems(build_act_context(act.pk))
    except Exception:
        logger.exception('Не удалось проверить документы о качестве акта %s', pk)
        problems = []
    if problems:
        messages.warning(request, 'Excel сформирован, но есть замечания: ' + '; '.join(problems) +
                         '. Проблемные места в файле отмечены «⚠».')

    log_event(request, 'Сформирован Excel акта', act, os.path.basename(path))
    return FileResponse(open(path, 'rb'), as_attachment=True, filename=os.path.basename(path))


@login_required
def act_registry(request, pk):
    act = get_object_or_404(HiddenWorkAct, pk=pk)
    if request.method != 'POST':
        return redirect('act_edit', pk=pk)
    try:
        from .registry_export import save_act_registry
        path, _ = save_act_registry(act)
    except Exception:
        logger.exception('Не удалось сформировать реестр акта %s', pk)
        messages.error(request, 'Реестр не сформирован. Повторите попытку.')
        return redirect('act_edit', pk=pk)
    log_event(request, 'Сформирован реестр к АОСР', act, os.path.basename(path))
    return FileResponse(open(path, 'rb'), as_attachment=True, filename=os.path.basename(path))


@login_required
def act_final_pdf(request, pk):
    act = get_object_or_404(HiddenWorkAct, pk=pk)
    try:
        from .final_act_export import build_final_act_pdf
        path, warnings = build_final_act_pdf(act)
    except Exception:
        logger.exception('Не удалось сформировать итоговый PDF акта %s', pk)
        messages.error(request, 'Итоговый PDF не сформирован. Повторите попытку.')
        return redirect('act_edit', pk=pk)
    for warning in warnings:
        messages.warning(request, warning)
    log_event(request, 'Сформирован итоговый PDF акта', act, os.path.basename(path))
    return FileResponse(open(path, 'rb'), as_attachment=True, filename=os.path.basename(path))


CORRECTED_EXT = ('.xlsx', '.xls', '.pdf', '.doc', '.docx', '.jpg', '.jpeg', '.png')
CORRECTED_MAX_MB = 20


@login_required
def act_corrected(request, pk):
    """Прикрепление/замена/удаление исправленного файла акта.
    Файл только хранится и отдаётся на скачивание — ничего из него в базу не загружается,
    автоматически сформированный Excel он не заменяет."""
    act = get_object_or_404(HiddenWorkAct, pk=pk)
    if request.method != 'POST':
        return redirect('act_edit', pk=pk)

    if request.POST.get('action') == 'remove':
        if act.corrected_file:
            act.corrected_file.delete(save=False)
            act.corrected_file = None
            act.save(update_fields=['corrected_file'])
            log_event(request, 'Удалён исправленный файл акта', act)
            messages.success(request, 'Исправленный файл убран.')
        return redirect('act_edit', pk=pk)

    f = request.FILES.get('file')
    if not f:
        messages.error(request, 'Выберите файл.')
    elif not f.name.lower().endswith(CORRECTED_EXT):
        messages.error(request, 'Поддерживаются Excel, Word, PDF и картинки (JPG, PNG).')
    elif f.size > CORRECTED_MAX_MB * 1024 * 1024:
        messages.error(request, f'Файл больше {CORRECTED_MAX_MB} МБ.')
    else:
        if act.corrected_file:
            act.corrected_file.delete(save=False)   # старый файл убрать с диска
        act.corrected_file = f
        act.save(update_fields=['corrected_file'])
        log_event(request, 'Прикреплён исправленный файл акта', act, f.name)
        messages.success(request, f'Исправленный файл «{f.name}» прикреплён. '
                                  'Сформированный автоматически Excel он не заменяет.')
    return redirect('act_edit', pk=pk)


@login_required
def act_corrected_file(request, pk):
    act = get_object_or_404(HiddenWorkAct, pk=pk)
    if not act.corrected_file:
        raise Http404('Исправленный файл не прикреплён')
    return FileResponse(act.corrected_file.open('rb'), as_attachment=True,
                        filename=os.path.basename(act.corrected_file.name))


@login_required
def act_copy(request, pk):
    """Новый акт на основе старого: реквизиты, подписанты и приложения.
    Замеры и материалы не копируются — они относятся к конкретной работе."""
    if request.method != 'POST':
        return redirect('act_edit', pk=pk)
    source = get_object_or_404(HiddenWorkAct, pk=pk)
    with transaction.atomic():
        new_act = HiddenWorkAct.objects.get(pk=source.pk)
        new_act.pk = None
        new_act.id = None
        new_act.act_number = _next_act_number(source.section)
        new_act.drawn_up_date = date.today()
        new_act.status = HiddenWorkAct.Status.DRAFT
        new_act.save()
        for s in ActSigner.objects.filter(act=source):
            ActSigner.objects.create(act=new_act, person=s.person, role_in_act=s.role_in_act)
        for a in ActAttachment.objects.filter(act=source):
            ActAttachment.objects.create(act=new_act, document=a.document)
    messages.success(request, f'Создан акт №{new_act.act_number} для раздела «{new_act.section.name}» '
                              f'({new_act.section.site.name}) на основе №{source.act_number}. '
                              'Подписанты и приложения скопированы, работы и материалы добавьте заново.')
    return redirect('act_edit', pk=new_act.pk)


@login_required
def act_delete(request, pk):
    act = get_object_or_404(HiddenWorkAct, pk=pk)
    if request.method == 'POST':
        number = act.act_number
        with transaction.atomic():
            if act.corrected_file:
                act.corrected_file.delete(save=False)
            ActMaterial.objects.filter(act=act).delete()
            ActSigner.objects.filter(act=act).delete()
            ActAttachment.objects.filter(act=act).delete()
            WorkVolume.objects.filter(act=act).update(act=None)
            act.delete()
        messages.success(request, f'Акт №{number} удалён. Его замеры остались и снова свободны.')
        return redirect('act_list')
    return render(request, 'core/confirm_delete.html', {
        'title': f'Удалить акт №{act.act_number} от {act.drawn_up_date:%d.%m.%Y}?',
        'details': f'Объект: {act.section.site.name}, раздел «{act.section.name}». '
                   'Подписанты, материалы и приложения этого акта будут удалены. '
                   'Замеры сохранятся и снова станут свободными.',
        'cancel_url': reverse('act_edit', args=[act.pk]),
    })


# ---------------------------------------------------------------------------
# Справочники (одни и те же страницы для всех — список настраивается в registry.py)
# ---------------------------------------------------------------------------

def _entry_or_404(slug):
    entry = get_entry(slug)
    if entry is None:
        raise Http404('Нет такого справочника')
    return entry


@login_required
def ref_index(request):
    groups = []
    for title, entries in get_entries():
        groups.append((title, [
            (e['slug'], e['model']._meta.verbose_name_plural, e['model'].objects.count())
            for e in entries
        ]))
    return render(request, 'core/ref_index.html', {'groups': groups})


@login_required
def ref_list(request, slug, site_pk=None):
    entry = _entry_or_404(slug)
    model = entry['model']
    field_names = {f.name for f in model._meta.fields}
    columns = [c for c in entry.get('columns', []) if c in field_names or c in VIRTUAL_COLUMNS] or ['id']

    locked_site = get_object_or_404(ConstructionSite, pk=site_pk) if site_pk else None
    if locked_site:
        set_current_site(request, locked_site)
    site = locked_site or get_current_site(request)
    if site_pk is None and slug == 'deliveries' and site:
        # Старый адрес «Поставки» ведёт в объектный раздел накладных.
        return redirect_to_site(request, 'site_deliveries', site)
    scoped_site = site if site and model_is_site_scoped(model) else None

    qs = model.objects.all()
    if entry.get('prefetch'):
        qs = qs.prefetch_related(*entry['prefetch'])
    if scoped_site:
        qs = filter_queryset(qs, model, scoped_site)

    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(_search_condition(model, q, entry.get('search', ()))).distinct()

    filters = []
    for name in entry.get('filters', []):
        if name not in field_names:
            continue
        field = model._meta.get_field(name)
        # Объект уже выбран — фильтр по нему не нужен и не должен раскрывать чужие объекты.
        if scoped_site and (name == 'site' or getattr(field, 'related_model', None) is ConstructionSite):
            continue
        selected = request.GET.get(f'f_{name}', '')
        boolean = isinstance(field, models.BooleanField)
        if field.choices:
            options = [(str(v), str(l)) for v, l in field.choices]
        elif isinstance(field, models.ForeignKey):
            related_qs = field.related_model.objects.all()
            if site:
                condition = related_scope_q(field.related_model, site)
                if condition is not None:
                    related_qs = related_qs.filter(condition)
            options = [(str(o.pk), str(o)) for o in related_qs[:300]]
            if field.null:
                options.insert(0, ('none', '— не указано —'))
        elif boolean:
            options = [('1', 'да'), ('0', 'нет')]
        elif isinstance(field, (models.CharField, models.TextField)):
            # фильтр по готовым значениям поля (корпус, категория, должность и т.п.)
            values_qs = model.objects.all()
            if scoped_site:
                values_qs = filter_queryset(values_qs, model, scoped_site)
            values = values_qs.order_by(name).values_list(name, flat=True).distinct()[:100]
            options = [(v, v) for v in values if v]
            if len(options) < 2:
                continue
        else:
            continue
        valid = {v for v, _ in options}
        if selected and selected not in valid:
            selected = ''
        if selected == 'none':
            qs = qs.filter(**{f'{name}__isnull': True})
        elif boolean and selected in ('0', '1'):
            qs = qs.filter(**{name: selected == '1'})
        elif selected:
            qs = qs.filter(**{name: selected})
        filters.append({'name': name, 'label': field_label(field), 'options': options, 'selected': selected})

    sort = request.GET.get('sort', '')
    sort_mapping = {}
    labels = []
    for c in columns:
        try:
            field = model._meta.get_field(c)
        except FieldDoesNotExist:
            labels.append(VIRTUAL_COLUMNS.get(c, c))
            continue
        labels.append(field_label(field))
        if isinstance(field, models.ForeignKey):
            sort_mapping[c] = f'{c}__{_related_sort_field(field.related_model)}'
        else:
            sort_mapping[c] = c
    qs = _apply_sort(qs, sort, sort_mapping, ('-pk',))

    page = Paginator(qs, 50).get_page(request.GET.get('page'))
    rows = [(obj.pk, [_display(obj, c) for c in columns]) for obj in page]
    primary_filters = filters[:3]
    extra_filters = filters[3:]

    return render(request, 'core/ref_list.html', {
        'entry': entry,
        'title': model._meta.verbose_name_plural,
        'headers': _sort_headers(request, columns, labels, sort, set(sort_mapping)),
        'rows': rows,
        'page': page,
        'q': q,
        'filters': primary_filters,
        'filters_more': extra_filters,
        'filters_more_open': any(f['selected'] for f in extra_filters),
        'qs': _querystring_without_page(request),
        'site': site,
        'locked_site': locked_site,
        'scoped_site': scoped_site,
        'list_url': request.path,
    })


@login_required
def ref_edit(request, slug, pk=None):
    entry = _entry_or_404(slug)
    model = entry['model']
    obj = get_object_or_404(model, pk=pk) if pk else None
    popup = bool(request.GET.get('popup') or request.POST.get('popup'))

    Form = make_form(model)
    data = request.POST if request.method == 'POST' else None
    files = request.FILES if request.method == 'POST' else None
    # ?поле=значение в адресе новой записи — предзаполнение (так «Объёмы» открывают решение по отклонению)
    initial = {k: v for k, v in request.GET.items() if k in Form.base_fields} if obj is None else None
    form = Form(data, files, instance=obj, initial=initial)

    # Формы объектных справочников показывают только данные своего объекта.
    effective_site = instance_site(obj) if obj is not None else get_current_site(request)
    if effective_site and (model is ConstructionSite or model_is_site_scoped(model)):
        if obj is not None:
            set_current_site(request, effective_site)
        restrict_form_to_site(form, effective_site)

    inline_fs = None
    if entry.get('inline'):
        child_model, fk_name = entry['inline']
        has_rows = obj is not None and child_model.objects.filter(**{fk_name: obj}).exists()
        FS = make_inline_formset(model, child_model, fk_name, extra=0 if has_rows else 1)
        inline_fs = FS(data, files, instance=obj or model(), prefix='inl')
        inline_fs.title = child_model._meta.verbose_name_plural

    if request.method == 'POST' and not getattr(request, '_cert_registry_preserve_form', False):
        if form.is_valid() and (inline_fs is None or inline_fs.is_valid()):
            try:
                with transaction.atomic():
                    obj = form.save()
                    if inline_fs is not None:
                        inline_fs.instance = obj
                        inline_fs.save()
            except ProtectedError:
                messages.error(request, 'Не сохранено: удаляемая строка уже используется в других записях.')
            else:
                if popup:
                    return render(request, 'core/popup_done.html', {
                        'label': str(obj), 'popup': True,
                        'model': model._meta.model_name, 'pk': obj.pk,
                    })
                log_event(request, f'Сохранено: {model._meta.verbose_name}', obj)
                messages.success(request, f'Сохранено: {obj}.')
                if slug == 'sites':
                    return redirect('site_edit', pk=obj.pk)
                return redirect(_safe_next(request) or reverse('ref_list', args=[slug]))
        else:
            messages.error(request, 'Не сохранено: проверьте поля, отмеченные красным.')

    return render(request, 'core/ref_form.html', {
        'entry': entry,
        'title': (f'{model._meta.verbose_name}: {obj}' if obj else f'Новая запись — {model._meta.verbose_name}'),
        'list_title': model._meta.verbose_name_plural,
        'form': form,
        'form_groups': grouped_fields(form, slug),
        'inline_fs': inline_fs,
        'obj': obj,
        'next': _safe_next(request) or '',
        'popup': popup,
    })


@login_required
def ref_delete(request, slug, pk):
    entry = _entry_or_404(slug)
    model = entry['model']
    obj = get_object_or_404(model, pk=pk)
    if request.method == 'POST':
        try:
            with transaction.atomic():
                if entry.get('inline'):
                    child_model, fk_name = entry['inline']
                    child_model.objects.filter(**{fk_name: obj}).delete()
                obj.delete()
        except ProtectedError as e:
            used_in = ', '.join(sorted({str(o._meta.verbose_name) for o in e.protected_objects}))
            messages.error(request, f'Нельзя удалить «{obj}»: на запись ссылаются ({used_in}). '
                                    'Если запись больше не нужна — переведите её в архив.')
            return redirect('ref_edit', slug=slug, pk=pk)
        log_event(request, f'Удалено: {model._meta.verbose_name}', obj)
        messages.success(request, f'Удалено: {obj}.')
        return redirect('ref_list', slug=slug)
    return render(request, 'core/confirm_delete.html', {
        'title': f'Удалить «{obj}»?',
        'details': f'Справочник: {model._meta.verbose_name_plural}.',
        'cancel_url': reverse('ref_edit', args=[slug, pk]),
    })


# ---------------------------------------------------------------------------
# Живой предпросмотр акта: документ со всеми подстановками, как в Excel
# ---------------------------------------------------------------------------

def _preview_date(value):
    for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime((value or '').strip(), fmt).date()
        except ValueError:
            continue
    return None


def _preview_decimal(value):
    try:
        return Decimal((value or '').strip().replace(',', '.'))
    except InvalidOperation:
        return None


def _preview_form_rows(act, data):
    """Несохранённые объекты из текущего состояния формы (POST), без валидации и записи."""
    def total(prefix):
        try:
            return int(data.get(f'{prefix}-TOTAL_FORMS', 0))
        except (ValueError, TypeError):
            return 0

    def deleted(prefix, i):
        return data.get(f'{prefix}-{i}-DELETE') in ('1', 'on', 'true')

    volumes = []
    for i in range(total('vol')):
        if deleted('vol', i):
            continue
        v = WorkVolume(
            act=act,
            work_type_id=data.get(f'vol-{i}-work_type') or None,
            location=data.get(f'vol-{i}-location', '').strip(),
            performed_by_id=data.get(f'vol-{i}-performed_by') or None,
            performance_date=_preview_date(data.get(f'vol-{i}-performance_date')),
            end_date=_preview_date(data.get(f'vol-{i}-end_date')),
            actual_volume=_preview_decimal(data.get(f'vol-{i}-actual_volume')),
            unit=data.get(f'vol-{i}-unit', '').strip(),
        )
        if any((v.work_type_id, v.location, v.performed_by_id, v.performance_date, v.end_date, v.unit)) \
                or v.actual_volume is not None:
            volumes.append(v)

    act_materials = []
    for i in range(total('mat')):
        if deleted('mat', i):
            continue
        item_id = data.get(f'mat-{i}-delivery_item') or None
        material_id = data.get(f'mat-{i}-material') or None
        qty = _preview_decimal(data.get(f'mat-{i}-written_off_volume'))
        if item_id or material_id or qty is not None:
            act_materials.append(ActMaterial(
                act=act, delivery_item_id=item_id, material_id=material_id, written_off_volume=qty))

    signers = []
    for i in range(total('sig')):
        if deleted('sig', i):
            continue
        person_id = data.get(f'sig-{i}-person') or None
        role = data.get(f'sig-{i}-role_in_act', '')
        if person_id or role:
            signers.append(ActSigner(act=act, person_id=person_id, role_in_act=role))

    act_attachments = []
    for i in range(total('att')):
        if deleted('att', i):
            continue
        doc_id = data.get(f'att-{i}-document') or None
        if doc_id:
            act_attachments.append(ActAttachment(act=act, document_id=doc_id))

    return volumes, act_materials, signers, act_attachments


@login_required
def act_preview(request, pk):
    """HTML-документ акта. GET — сохранённое состояние, POST — текущее состояние формы,
    ничего при этом не сохраняется. Подстановки — те же, что при генерации Excel."""
    from .act_export import (_org_line, _fmt_qty, _format_date, materials_need_appendix, APPENDIX_NOTE,
                             APPENDIX_ATTACHMENT_NOTE, signer_line1, signer_line2)
    from .services import build_act_context, build_act_context_preview

    act = get_object_or_404(HiddenWorkAct.objects.select_related('section', 'section__site'), pk=pk)
    if request.method == 'POST':
        for name in ('act_number', 'next_work_override'):
            if name in request.POST:
                setattr(act, name, request.POST.get(name, '').strip())
        if 'drawn_up_date' in request.POST:
            act.drawn_up_date = _preview_date(request.POST.get('drawn_up_date'))
        if 'next_work_type' in request.POST:
            act.next_work_type = WorkType.objects.filter(pk=request.POST.get('next_work_type') or 0).first()
        rows = _preview_form_rows(act, request.POST)
        context = build_act_context_preview(act, *rows)
    else:
        context = build_act_context(act.pk)

    # предпросмотр показывает шапку снимка, а правки скрытого блока — сразу, без сохранения
    header = _header_with_overrides(
        header_for_act(act), request.POST if request.method == 'POST' else None)

    # Точный предпросмотр: настоящий Excel → PDF → картинки страниц.
    # Кэш по содержимому формы — повторная генерация не нужна, скачивать ничего не надо.
    from .preview import PreviewUnavailable, build_preview_pages, cache_key
    key = cache_key(act.pk, request.POST if request.method == 'POST' else None)
    try:
        pages = build_preview_pages(act.pk, key, context, header)
        return render(request, 'core/act_preview_pdf.html', {'pages': pages, 'key': key, 'act_id': act.pk})
    except PreviewUnavailable:
        pass  # нет LibreOffice — ниже HTML-вариант с теми же подстановками

    materials_in_registry = context['has_registry']
    materials_in_appendix = not materials_in_registry and materials_need_appendix(context['materials'])
    registry_note = (f"Перечень документов о качестве приведён в реестре к АОСР № {context['act_number']} "
                     f"от {_format_date(context['drawn_up_date'])}")
    attachment_texts = context['attachments']
    if materials_in_registry and context['cert_attachments']:
        attachment_texts = context['attachments'][context['cert_attachments']:]
    elif materials_in_appendix and context['cert_attachments']:
        attachment_texts = ([APPENDIX_ATTACHMENT_NOTE]
                            + context['attachments'][context['cert_attachments']:])

    material_texts = [
        f"{m['name']} {_fmt_qty(m['quantity'])} {m['unit']}. {m['certificate_text']}".strip()
        for m in context['materials']
    ]

    signer_blocks = []
    for role, label in ActSigner.Role.choices:
        s = context['signers'].get(role)
        if not s:
            continue
        line1 = signer_line1(role, s)
        signer_blocks.append({'label': label, 'line1': line1, 'line2': signer_line2(s),
                              'name': s['full_name']})

    return render(request, 'core/act_preview.html', {
        'act': context,
        'header': header,
        'customer_line': _org_line(header['customer_name'], header['customer_ogrn'], header['customer_inn']),
        'contractor_line': _org_line(header['contractor_name'], header['contractor_ogrn'], header['contractor_inn']),
        'designer_line': _org_line(header['designer_name'], header['designer_ogrn'], header['designer_inn']),
        'contractor_sro': (f"{header['contractor_sro_name']}, {header['contractor_sro_code']} "
                           f"от {_format_date(header['contractor_sro_date'])}") if header['contractor_sro_name'] else '',
        'designer_sro': (f"{header['designer_sro_name']}, {header['designer_sro_code']} "
                         f"от {_format_date(header['designer_sro_date'])}") if header['designer_sro_name'] else '',
        'materials_in_registry': materials_in_registry,
        'materials_in_appendix': materials_in_appendix,
        'registry_note': registry_note,
        'appendix_note': APPENDIX_NOTE,
        'material_texts': material_texts,
        'material_names': list(dict.fromkeys(m['name'] for m in context['materials'] if m.get('name'))),
        'attachment_texts': attachment_texts,
        'schemes': [a for a in attachment_texts if 'схем' in a.lower()],
        'copies': context['copies_count'] or 4,
        'signer_blocks': signer_blocks,
    })

@login_required
def act_preview_file(request, pk, key, name):
    """Страница-картинка из кэша предпросмотра (только pN.png по hex-ключу)."""
    from .preview import preview_dir
    if not re.fullmatch(r'[0-9a-f]{40}', key) or not re.fullmatch(r'p\d+\.png', name):
        raise Http404
    path = os.path.join(preview_dir(pk, key), name)
    if not os.path.isfile(path):
        raise Http404
    return FileResponse(open(path, 'rb'), content_type='image/png')


# ---------------------------------------------------------------------------
# Карточка объекта: реквизиты и подписанты по умолчанию для новых актов
# ---------------------------------------------------------------------------

@login_required
def site_edit(request, pk):
    site = get_object_or_404(ConstructionSite.objects.select_related(
        'customer', 'general_contractor', 'designer'), pk=pk)
    set_current_site(request, site)
    data = request.POST if request.method == 'POST' else None
    form = make_form(ConstructionSite)(data, instance=site)

    has_signers = SiteSigner.objects.filter(site=site).exists()
    SignerFS = make_inline_formset(ConstructionSite, SiteSigner, 'site', extra=0 if has_signers else 1)
    signers = SignerFS(data, instance=site, prefix='sig')

    if request.method == 'POST':
        if form.is_valid() and signers.is_valid():
            with transaction.atomic():
                form.save()
                for obj in signers.save(commit=False):
                    obj.site = site
                    obj.save()
                for obj in signers.deleted_objects:
                    obj.delete()
            log_event(request, 'Сохранена карточка объекта', site)
            messages.success(request, 'Сохранено. Подписанты будут подставляться в новые акты этого объекта.')
            return redirect('site_edit', pk=site.pk)
        messages.error(request, 'Не сохранено: проверьте поля, отмеченные красным.')

    return render(request, 'core/site_edit.html', {
        'site': site, 'form': form, 'signers': signers,
        'form_groups': grouped_fields(form, 'sites'),
    })
