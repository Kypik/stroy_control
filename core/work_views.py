"""
core/work_views.py

Основа для остальных экранов из описания проекта:
  /attention/            — «Требует внимания»: все найденные проблемы со ссылками
  /deliveries/matching/  — сопоставление строк накладных с материалами (+ запоминание синонимов),
                           ввод строк накладной текстом
  /journal/              — журнал входного контроля (собирается сам) + выгрузка в Excel
  /volumes/compare/      — смета / факт / отклонения, переход к решению по отклонению
"""

import logging
from datetime import date, datetime
from io import BytesIO
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from . import checks, journal_excel, matching
from .events import log_event
from .models import (
    ConstructionSite, Counterparty, DeliveryItem, DeliveryNote, DocumentationSection,
    IncomingInspection, Material,
)
from .site_context import (
    get_current_site, legacy_site_from_request, redirect_to_site, set_current_site,
)


logger = logging.getLogger(__name__)


def _date_param(value):
    """Дата из адресной строки: ISO (гггг-мм-дд) или по-русски (дд.мм.гггг)."""
    if not value:
        return None
    for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Требует внимания
# ---------------------------------------------------------------------------

@login_required
def attention(request, site_pk=None):
    if site_pk is None:
        site = get_current_site(request)
        if site:
            return redirect_to_site(request, 'site_attention', site, drop=())
        return redirect('attention_all')

    site = get_object_or_404(ConstructionSite, pk=site_pk)
    set_current_site(request, site)
    groups = checks.attention(site)
    return render(request, 'core/attention.html', {
        'groups': groups, 'total': sum(len(g['items']) for g in groups),
        'site': site, 'locked_site': site,
    })


@login_required
def attention_all(request):
    """Общий экран «Внимание» без ограничения объектом."""
    groups = checks.attention()
    return render(request, 'core/attention.html', {
        'groups': groups, 'total': sum(len(g['items']) for g in groups),
        'site': None,
    })


# ---------------------------------------------------------------------------
# Сопоставление строк накладных
# ---------------------------------------------------------------------------

@login_required
def delivery_matching(request, site_pk=None):
    if site_pk is None:
        site = get_current_site(request)
        if site:
            return redirect_to_site(request, 'site_delivery_matching', site)
        return redirect('site_list')

    site = get_object_or_404(ConstructionSite, pk=site_pk)
    set_current_site(request, site)

    show = request.GET.get('show', 'todo')          # todo — несопоставленные и непроверенные, all — все
    items = DeliveryItem.objects.filter(delivery_note__site=site) \
        .select_related('delivery_note', 'delivery_note__supplier', 'material') \
        .order_by('-delivery_note__delivery_date', 'delivery_note__number', 'pk')
    if show != 'all':
        items = items.exclude(matching_status=DeliveryItem.Status.CONFIRMED)
    q = request.GET.get('q', '').strip()
    if q:
        items = items.filter(Q(original_text__icontains=q) | Q(material__name__icontains=q)
                             | Q(delivery_note__number__icontains=q)
                             | Q(delivery_note__supplier__short_name__icontains=q))

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'auto':
            n = matching.auto_match(DeliveryItem.objects.filter(
                delivery_note__site=site, material__isnull=True))
            messages.success(request, f'Подобрано автоматически: {n}. Проверьте строки со статусом «автоматически опознано».')
            log_event(request, 'Автосопоставление накладных', site, f'подобрано {n}')
        elif action == 'save':
            remember = bool(request.POST.get('remember'))
            changed = 0
            with transaction.atomic():
                for key, value in request.POST.items():
                    if not key.startswith('item_') or not value:
                        continue
                    item = DeliveryItem.objects.filter(
                        pk=key[5:], delivery_note__site=site).first()
                    material = Material.objects.filter(pk=value).first()
                    if item and material and (item.material_id != material.pk
                                              or item.matching_status != DeliveryItem.Status.CONFIRMED):
                        matching.confirm(item, material, remember)
                        changed += 1
            messages.success(request, f'Подтверждено строк: {changed}.')
            log_event(request, 'Сопоставление накладных вручную', site, f'строк {changed}')
        elif action == 'paste':
            note = get_object_or_404(DeliveryNote, pk=request.POST.get('note'), site=site)
            rows, errors = matching.parse_lines(request.POST.get('lines'))
            for e in errors:
                messages.error(request, e)
            if rows and not errors:
                with transaction.atomic():
                    created = [DeliveryItem.objects.create(delivery_note=note, original_text=name, quantity=qty, unit=unit)
                               for name, qty, unit in rows]
                    n = matching.auto_match(created)
                messages.success(request, f'Добавлено строк в накладную №{note.number}: {len(created)}, '
                                          f'материал подобран для {n}.')
                log_event(request, 'Строки накладной введены текстом', note, f'строк {len(created)}')
        return redirect(request.get_full_path())

    # выпадающий список материалов по категориям — общий для всех строк
    groups = {}
    for m in Material.objects.exclude(status='archived').order_by('name'):
        groups.setdefault((m.category or '').strip() or 'Без категории', []).append((m.pk, m.name))
    return render(request, 'core/delivery_matching.html', {
        'items': items[:300],
        'show': show,
        'q': q,
        'material_groups': sorted(groups.items(), key=lambda g: (g[0] == 'Без категории', g[0].lower())),
        'notes': DeliveryNote.objects.filter(site=site).order_by('-delivery_date')[:100],
        'site': site,
        'locked_site': site,
    })


# ---------------------------------------------------------------------------
# Журнал входного контроля
# ---------------------------------------------------------------------------

JOURNAL_COLUMNS = [
    '№ п/п', 'Дата поставки', 'Наименование материалов и конструкций', 'Ед. изм.', 'Кол-во',
    'Поставщик', 'Наименование сопроводительного документа (сертификаты, декларации, паспорта качества)',
    'Вид контроля (визуальный, инструментальный, лабораторный)', 'Результат контроля',
    'Подпись лица, осуществляющего контроль',
]


def journal_rows(site_id, date_from=None, date_to=None, supplier_id=None):
    """Журнал не хранится, а собирается из накладных, сертификатов и записей входного контроля."""
    from .services import certificate_text, pick_certificate

    items = DeliveryItem.objects.filter(delivery_note__site_id=site_id) \
        .select_related('delivery_note', 'delivery_note__supplier', 'material') \
        .order_by('delivery_note__delivery_date', 'delivery_note__number', 'pk')
    if date_from:
        items = items.filter(delivery_note__delivery_date__gte=date_from)
    if date_to:
        items = items.filter(delivery_note__delivery_date__lte=date_to)
    if supplier_id:
        items = items.filter(delivery_note__supplier_id=supplier_id)

    inspections = {}
    for ins in IncomingInspection.objects.filter(delivery_item__in=items).select_related('inspector') \
            .order_by('inspection_date', 'pk'):
        inspections[ins.delivery_item_id] = ins        # последняя запись по строке

    rows = []
    for n, item in enumerate(items, start=1):
        note = item.delivery_note
        cert_text, problem = '', ''
        if item.material:
            cert, problem = pick_certificate(item.material, note.delivery_date)
            if cert and not problem:
                cert_text = certificate_text(cert)
        # документ из импортированной таблицы важнее автоматически подобранного
        if item.certificate_text:
            cert_text, problem = item.certificate_text, ''
        elif problem:
            cert_text = problem
        ins = inspections.get(item.pk)
        rows.append({
            'n': n, 'date': note.delivery_date, 'note': note.number, 'supplier': str(note.supplier),
            'material': str(item.material or item.original_text),
            'qty': item.quantity if item.quantity is not None else '', 'unit': item.unit,
            'certificate': cert_text, 'problem': bool(problem) or item.material is None,
            'control_type': ins.control_type if ins else '',
            'result': (ins.inspection_result + ('' if ins.usage_permission else ' (не допущено)')) if ins else '',
            'inspector': (ins.inspector_name or (str(ins.inspector) if ins.inspector else '')) if ins else '',
            'inspection_date': ins.inspection_date if ins else None,
            'item_id': item.pk,
        })
    return rows


def _journal_xlsx(site, rows, date_from, date_to):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side

    wb = Workbook()
    ws = wb.active
    ws.title = 'Журнал ВК'
    period = ''
    if date_from or date_to:
        period = f" за период {date_from:%d.%m.%Y} – " if date_from else ' по '
        period += f"{date_to:%d.%m.%Y}" if date_to else 'настоящее время'
    ws.append([f'Журнал входного контроля материалов — {site.name}{period}'])
    ws['A1'].font = Font(bold=True, size=12)
    ws.append([])
    ws.append(JOURNAL_COLUMNS)
    thin = Side(style='thin')
    for r in rows:
        ws.append([r['n'], r['date'], r['material'], r['unit'],
                   float(r['qty']) if r['qty'] not in (None, '') else None, r['supplier'],
                   r['certificate'], r['control_type'], r['result'], r['inspector']])
    widths = [6, 12, 45, 8, 9, 26, 55, 22, 24, 26]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(row=3, column=i).column_letter].width = w
    for row in ws.iter_rows(min_row=3, max_row=ws.max_row):
        for cell in row:
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            cell.alignment = Alignment(wrap_text=True, vertical='top')
            if cell.row == 3:
                cell.font = Font(bold=True)
            if hasattr(cell.value, 'year'):
                cell.number_format = 'DD.MM.YYYY'
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


@login_required
def journal(request, site_pk=None):
    if site_pk is None:
        site = legacy_site_from_request(request)
        if site:
            return redirect_to_site(request, 'site_journal', site)
        return redirect('site_list')

    site = get_object_or_404(ConstructionSite, pk=site_pk)
    set_current_site(request, site)
    sites = ConstructionSite.objects.order_by('name')
    date_from, date_to = _date_param(request.GET.get('from')), _date_param(request.GET.get('to'))
    supplier = request.GET.get('supplier', '')
    only_problems = bool(request.GET.get('problems'))
    q = request.GET.get('q', '').strip().lower()
    rows = journal_rows(site.pk, date_from, date_to, supplier or None) if site else []
    problem_count = sum(1 for r in rows if r['problem'])
    if only_problems:
        rows = [r for r in rows if r['problem']]
    if q:
        rows = [r for r in rows if any(q in str(r[k]).lower() for k in
                                       ('note', 'supplier', 'material', 'certificate', 'result', 'inspector'))]
    if only_problems or q:
        for i, r in enumerate(rows, start=1):
            r['n'] = i

    if site and request.GET.get('export') == 'xlsx':
        log_event(request, 'Выгрузка журнала входного контроля', site, f'строк {len(rows)}')
        response = HttpResponse(_journal_xlsx(site, rows, date_from, date_to),
                                content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        name = quote(f'Журнал_ВК_{site.name}.xlsx')
        response['Content-Disposition'] = f"attachment; filename*=UTF-8''{name}"
        return response

    suppliers = Counterparty.objects.filter(
        pk__in=DeliveryNote.objects.filter(site=site).values('supplier_id')
    ).order_by('short_name') if site else []
    return render(request, 'core/journal.html', {
        'sites': sites, 'site': site, 'rows': rows, 'columns': JOURNAL_COLUMNS,
        'date_from': request.GET.get('from', ''), 'date_to': request.GET.get('to', ''),
        'suppliers': suppliers, 'supplier': supplier, 'only_problems': only_problems,
        'q': request.GET.get('q', '').strip(),
        'problems': problem_count,
        'locked_site': site,
    })


@login_required
def journal_import(request):
    """Загрузка журнала из Excel: строки становятся накладными, позициями и записями контроля."""
    if request.method != 'POST':
        return redirect('journal')
    site = get_object_or_404(ConstructionSite, pk=request.POST.get('site') or 0)
    set_current_site(request, site)
    upload = request.FILES.get('file')
    if not upload:
        messages.error(request, 'Выберите файл Excel (.xlsx).')
    elif not upload.name.lower().endswith('.xlsx'):
        messages.error(request, 'Поддерживается только формат .xlsx.')
    else:
        try:
            result = journal_excel.import_xlsx(upload, site)
        except ValueError as exc:
            messages.error(request, str(exc))
        except Exception:
            logger.exception('Не удалось импортировать журнал входного контроля')
            messages.error(request, 'Не удалось прочитать таблицу — проверьте, что это файл журнала Excel.')
        else:
            messages.success(
                request,
                f"Импортировано строк: {result['rows']}. Новых накладных: {result['notes']}, "
                f"материалов: {result['materials']}, контрагентов: {result['counterparties']}."
            )
            if result['errors']:
                messages.warning(request, 'Пропущенные строки: ' + ' '.join(result['errors'][:5])
                                 + (f' и ещё {len(result["errors"]) - 5}.' if len(result['errors']) > 5 else ''))
            log_event(request, 'Импорт журнала входного контроля', site, f"строк {result['rows']}")
    return redirect('site_journal', pk=site.pk)


# ---------------------------------------------------------------------------
# Смета / факт
# ---------------------------------------------------------------------------

@login_required
def volumes_compare(request, site_pk=None):
    if site_pk is None:
        site = legacy_site_from_request(request)
        if site:
            return redirect_to_site(request, 'site_volumes', site)
        return redirect('site_list')

    site = get_object_or_404(ConstructionSite, pk=site_pk)
    set_current_site(request, site)
    sites = ConstructionSite.objects.order_by('name')
    sections = DocumentationSection.objects.filter(site=site).order_by('order_number')
    section_id = request.GET.get('section') or ''
    only_over = bool(request.GET.get('over'))
    rows = checks.compare_rows(site.pk, section_id or None)
    if only_over:
        rows = [r for r in rows if r['over']]
    q = request.GET.get('q', '').strip().lower()
    if q:
        rows = [r for r in rows if q in str(r['line'].room).lower()
                or q in str(r['line'].work_type).lower()
                or q in (r['line'].estimate_code or '').lower()]
    for r in rows:
        r['resolve_url'] = (reverse('ref_create', args=['deviations'])
                            + f"?estimate_line={r['line'].pk}&deviation_amount={r['diff']}"
                            + f"&decision_date={date.today():%Y-%m-%d}"
                            + f"&next={reverse('site_volumes', args=[site.pk])}")
    return render(request, 'core/volumes_compare.html', {
        'sites': sites, 'site': site, 'sections': sections, 'section_id': section_id,
        'rows': rows, 'only_over': only_over, 'q': request.GET.get('q', '').strip(),
        'locked_site': site,
    })
