"""
core/checks.py

Проверки для экрана «Требует внимания» и сравнение смета/факт.
Каждая проверка — отдельная функция, возвращающая список (текст, ссылка).
Новую проверку достаточно дописать и добавить в CHECKS внизу.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.urls import reverse

from .models import (
    Certificate, CertificateUpload, DeliveryItem, DeviationResolution, EstimateLine,
    HiddenWorkAct, Person, WorkVolume,
)

logger = logging.getLogger(__name__)

SOON_DAYS = 30   # за сколько дней предупреждать об окончании срока


def _ref(slug, pk):
    return reverse('ref_edit', args=[slug, pk])


def _certs_with_materials():
    """Сертификаты с заранее подгруженными материалами — чтобы подпись не требовала запросов."""
    return Certificate.objects.prefetch_related('materialcertificate_set__material')


def _cert_label(cert):
    """Тип и номер документа + материал: номера сертификатов пользователь не помнит наизусть."""
    label = f'{cert.document_type} №{cert.document_number}'
    subject = cert.materials_label()
    if not subject and cert.product_text:
        product = ' '.join(cert.product_text.split())
        subject = product[:80] + ('…' if len(product) > 80 else '')
    return f'{label} — {subject}' if subject else label


# ---------------------------------------------------------------------------
# Проверки
# ---------------------------------------------------------------------------

def check_certificates():
    today, soon = date.today(), date.today() + timedelta(days=SOON_DAYS)
    result = []
    certs = _certs_with_materials().exclude(document_type=Certificate.DocType.REFUSAL) \
        .exclude(verification_status=Certificate.Status.REJECTED).filter(expiry_date__isnull=False)
    for c in certs.filter(expiry_date__lt=today, materialcertificate__isnull=False).distinct():
        result.append((f'{_cert_label(c)} — срок действия истёк {c.expiry_date:%d.%m.%Y}',
                       _ref('certificates', c.pk)))
    for c in certs.filter(expiry_date__gte=today, expiry_date__lte=soon):
        result.append((f'{_cert_label(c)} — срок действия истекает {c.expiry_date:%d.%m.%Y}',
                       _ref('certificates', c.pk)))
    for c in _certs_with_materials().filter(registry_status__in=['ended', 'suspended', 'not_found']):
        result.append((f'{_cert_label(c)}: в реестре — {c.get_registry_status_display()}',
                       _ref('certificates', c.pk)))
    return result


def check_uploads():
    result = []
    for up in CertificateUpload.objects.filter(status=CertificateUpload.Status.DONE).select_related('document'):
        total, saved = len(up.result.get('documents', [])), len(up.result.get('saved', {}))
        if saved < total:
            name = up.document.path_to_file.name.rsplit('/', 1)[-1]
            result.append((f'Загрузка «{name}»: проверено {saved} из {total} документов, '
                           f'осталось проверить — {total - saved}', reverse('cert_review', args=[up.pk])))
    return result


def check_delivery_items(site=None):
    items = DeliveryItem.objects.all()
    url = reverse('delivery_matching')
    if site is not None:
        items = items.filter(delivery_note__site=site)
        url = reverse('site_delivery_matching', args=[site.pk])
    n_none = items.filter(material__isnull=True).count()
    n_auto = items.filter(matching_status=DeliveryItem.Status.AUTO_IDENTIFIED).count()
    result = []
    if n_none:
        examples = list(items.filter(material__isnull=True)
                        .values_list('original_text', flat=True)[:3])
        text = f'Строк накладных без материала: {n_none}'
        if examples:
            text += ' (например: ' + ', '.join(f'«{e}»' for e in examples if e) + ')'
        result.append((text, url))
    if n_auto:
        result.append((f'Строк накладных, сопоставленных автоматически и не проверенных: {n_auto}', url))
    return result


def check_persons(site=None):
    soon = date.today() + timedelta(days=SOON_DAYS)
    persons = Person.objects.filter(authority_end_date__isnull=False, authority_end_date__lte=soon) \
        .exclude(status='archived').select_related('organization')
    if site is not None:
        persons = persons.filter(
            Q(sitesigner__site=site) | Q(actsigner__act__section__site=site)
        ).distinct()
    return [(f'{p.display_name}: полномочия '
             f'{"закончились" if p.authority_end_date < date.today() else "заканчиваются"} '
             f'{p.authority_end_date:%d.%m.%Y}', _ref('persons', p.pk))
            for p in persons]


def check_acts(site=None):
    from .views import act_blocking
    result = []
    acts = HiddenWorkAct.objects.exclude(status=HiddenWorkAct.Status.ACCEPTED) \
        .select_related('section__site')
    if site is not None:
        acts = acts.filter(section__site=site)
    for act in acts:
        problems = act_blocking(act)
        if problems:
            label = (f'Акт №{act.act_number} — {act.section.site.name}, '
                     f'раздел «{act.section.name}»')
            result.append((f'{label}: {problems[0]}'
                           + (f' и ещё {len(problems) - 1}' if len(problems) > 1 else ''),
                           reverse('act_edit', args=[act.pk])))
    return result


def check_deviations(site=None):
    rows = compare_rows(site.pk if site is not None else None)
    return [(f'{r["line"].room} ({r["line"].room.site.name}) — {r["line"].work_type}: '
             f'факт отличается от сметы на {r["percent"]:+.1f}% '
             f'(допустимо ±{r["allowed"]}%)',
             reverse('site_volumes', args=[r['line'].room.site_id]))
            for r in rows if r['over'] and not r['resolution']]


CHECKS = [
    ('Акты: проблемы с документами о качестве', 'warn', check_acts),
    ('Сертификаты', 'error', check_certificates),
    ('Непроверенные загрузки сертификатов', 'warn', check_uploads),
    ('Накладные', 'warn', check_delivery_items),
    ('Объёмы сверх сметы без решения', 'warn', check_deviations),
    ('Полномочия подписантов', 'warn', check_persons),
]


SITE_CHECKS = [
    ('Акты: проблемы с документами о качестве', 'warn', check_acts),
    ('Накладные', 'warn', check_delivery_items),
    ('Объёмы сверх сметы без решения', 'warn', check_deviations),
    ('Полномочия подписантов', 'warn', check_persons),
]


def attention(site=None):
    """[{'title', 'level', 'items': [(текст, ссылка)]}] — только непустые группы.
    С объектом проверяются только те вещи, которые к нему относятся; сертификаты
    и загрузки пока остаются общими."""
    groups = []
    active_checks = SITE_CHECKS if site is not None else CHECKS
    for title, level, func in active_checks:
        try:
            items = func(site) if site is not None else func()
        except Exception:
            logger.exception('Проверка «%s» не выполнилась', title)
            items, level = [('Не удалось выполнить проверку. Попробуйте обновить страницу позже.', '')], 'error'
        if items:
            groups.append({'title': title, 'level': level, 'items': items})
    return groups


# ---------------------------------------------------------------------------
# Смета / факт
# ---------------------------------------------------------------------------

def compare_rows(site_id=None, section_id=None):
    """Строки сравнения по каждой сметной строке: смета, факт (сумма замеров по помещению и виду работ),
    разница, процент, допустимое отклонение, последнее решение по отклонению."""
    lines = EstimateLine.objects.select_related('room', 'room__site', 'work_type', 'work_type__section')
    volumes = WorkVolume.objects.all()
    if site_id:
        lines = lines.filter(room__site_id=site_id, work_type__section__site_id=site_id)
        volumes = volumes.filter(
            Q(room__site_id=site_id) | Q(work_type__section__site_id=site_id))
    if section_id:
        lines = lines.filter(work_type__section_id=section_id)

    facts = {(v['room_id'], v['work_type_id']): v['total'] for v in
             volumes.values('room_id', 'work_type_id').annotate(total=Sum('actual_volume'))}
    resolutions = {}
    for r in DeviationResolution.objects.order_by('decision_date', 'pk'):
        resolutions[r.estimate_line_id] = r      # последнее решение по строке

    rows = []
    for line in lines.order_by('work_type__section__order_number', 'work_type__order_number', 'room__project_number'):
        estimate = line.estimated_volume or Decimal('0')
        fact = facts.get((line.room_id, line.work_type_id)) or Decimal('0')
        diff = fact - estimate
        percent = float(diff / estimate * 100) if estimate else 0.0
        allowed = line.work_type.allowed_deviation_percent or Decimal('0')
        rows.append({
            'line': line, 'estimate': estimate, 'fact': fact, 'diff': diff, 'percent': percent,
            'allowed': allowed, 'over': abs(percent) > float(allowed),
            'resolution': resolutions.get(line.pk),
        })
    return rows
