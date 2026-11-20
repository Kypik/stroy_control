"""
core/cert_views.py

Загрузка документов о качестве (сертификаты, декларации, отказные письма) и проверка распознанного.

  /certificates/upload/                 — загрузить файлы, список загрузок
  /certificates/upload/<id>/            — проверка: скан слева, распознанные поля справа
  /certificates/upload/<id>/status/     — ход распознавания (для автообновления страницы)
  /certificates/upload/<id>/page/<n>/   — картинка страницы
  /certificates/upload/<id>/file/       — исходный файл
  /certificates/upload/<id>/rerun/      — распознать заново
  /certificates/upload/<id>/materials/<i>/ — список материалов для карточки (обновляется после «+ материал»)

Распознанное ничего не меняет в базе, пока инженер не нажмёт «Сохранить».
"""

import hashlib
import os
import re
from urllib.parse import urlencode

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import cert_jobs
from .events import log_event
from .models import (
    Certificate, CertificateUpload, ConstructionSite, Counterparty, Documents,
    Material, MaterialCertificate,
)
from .site_context import set_current_site

ALLOWED_EXT = ('.pdf', '.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp')
MAX_SIZE_MB = 50
UPLOAD_DOC_TYPE = 'Документ о качестве (скан)'
NO_CATEGORY = 'Без категории'
FSA_SEARCH = {
    Certificate.DocType.CERTIFICATE: 'https://pub.fsa.gov.ru/rss/certificate',
    Certificate.DocType.DECLARATION: 'https://pub.fsa.gov.ru/rds/declaration',
}


# ---------------------------------------------------------------------------
# Сопоставление с тем, что уже есть в базе
# ---------------------------------------------------------------------------

_LAT_TO_CYR = str.maketrans('ABCEHKMOPTXY', 'АВСЕНКМОРТХУ')


def number_key(number):
    """Номер для сравнения: без пробелов, заглавными, латиница-двойник → кириллица."""
    return re.sub(r'[\s№N]+', '', (number or '').upper().translate(_LAT_TO_CYR))


def find_existing_certificate(number):
    key = number_key(number)
    if not key:
        return None
    for cert in Certificate.objects.all().only('id', 'document_number'):
        if number_key(cert.document_number) == key:
            return Certificate.objects.get(pk=cert.pk)
    return None


def _name_key(name):
    name = (name or '').upper().replace('Ё', 'Е')
    name = re.sub(r'\b(ООО|АО|ПАО|ЗАО|ОАО|ИП|ФГУП|ГУП|МУП)\b', ' ', name)
    return re.sub(r'[^0-9А-ЯA-Z]+', '', name)


def find_counterparty(org):
    """Контрагент из справочника по ИНН, если ИНН не прочитан — по названию."""
    org = org or {}
    inn = (org.get('inn') or '').strip()
    if inn:
        found = Counterparty.objects.filter(inn=inn).first()
        if found:
            return found
    key = _name_key(org.get('short_name') or org.get('full_name'))
    if len(key) >= 3:
        for cp in Counterparty.objects.all():
            if key in (_name_key(cp.short_name), _name_key(cp.full_name)):
                return cp
    return None


_WORD = re.compile(r'[А-Яа-яЁёA-Za-z]{4,}')
_STOP = {'продукция', 'изготовлена', 'изготовленная', 'соответствии', 'требованиями', 'серийный',
         'выпуск', 'марки', 'марка', 'типа', 'вида', 'партия', 'код', 'общего', 'назначения', 'изделия',
         'материалы', 'строительные', 'технические', 'условия', 'основе', 'числе'}


def _stems(text):
    return {w.lower()[:5] for w in _WORD.findall(text or '') if w.lower() not in _STOP}


def _std_key(text):
    return re.sub(r'[\s\-–.]+', '', (text or '').upper().translate(_LAT_TO_CYR))


def suggest_materials(doc, manufacturer=None, limit=6):
    """Материалы, к которым, скорее всего, относится документ. Только подсказка — решает инженер.
    Возвращает [(материал, очки, [причины])], сначала самые вероятные."""
    inn = (doc.get('manufacturer') or {}).get('inn') or (manufacturer.inn if manufacturer else '')
    product = _stems(doc.get('product'))
    standards = {_std_key(s) for s in doc.get('standards') or []}

    result = []
    for m in Material.objects.select_related('manufacturer', 'standard').exclude(status='archived'):
        score, why = 0, []
        if (manufacturer and m.manufacturer_id == manufacturer.id) or (inn and m.manufacturer and m.manufacturer.inn == inn):
            score += 3
            why.append('тот же производитель')
        if m.standard and standards and any(k and (k in _std_key(m.standard.designation)
                                                   or _std_key(m.standard.designation) in k)
                                            for k in standards):
            score += 3
            why.append(f'норматив {m.standard.designation}')
        common = product & _stems(m.name)
        if common:
            score += min(3, len(common))
            why.append('совпадает название')
        if score >= 3:
            result.append((m, score, why))
    result.sort(key=lambda r: (-r[1], r[0].name))
    return result[:limit]


def _material_groups():
    groups = {}
    for m in Material.objects.exclude(status='archived').order_by('name'):
        groups.setdefault((m.category or '').strip() or NO_CATEGORY, []).append(m)
    return [(c, groups[c]) for c in sorted(groups, key=lambda c: (c == NO_CATEGORY, c.lower()))]


# ---------------------------------------------------------------------------
# Форма одной карточки (один документ из файла)
# ---------------------------------------------------------------------------

DateInput = lambda: forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')


class CertCardForm(forms.Form):
    use_required_attribute = False   # иначе браузер не даст отправить страницу из-за пропущенных карточек
    save_it = forms.BooleanField(required=False, label='Сохранить этот документ')
    document_type = forms.ChoiceField(label='Тип документа', choices=Certificate.DocType.choices)
    document_number = forms.CharField(label='Номер', max_length=200)
    issue_date = forms.DateField(label='Дата выдачи', widget=DateInput())
    expiry_date = forms.DateField(label='Действует до', required=False, widget=DateInput())
    manufacturer = forms.ChoiceField(label='Изготовитель', required=False)
    new_short_name = forms.CharField(label='Краткое наименование', required=False, max_length=200)
    new_full_name = forms.CharField(label='Полное наименование', required=False, max_length=200)
    new_inn = forms.CharField(label='ИНН', required=False, max_length=12)
    new_ogrn = forms.CharField(label='ОГРН', required=False, max_length=15)
    new_address = forms.CharField(label='Адрес', required=False, max_length=300)
    issuer = forms.CharField(required=False, max_length=300, widget=forms.HiddenInput)
    product_text = forms.CharField(label='Продукция (как в документе)', required=False,
                                   widget=forms.Textarea(attrs={'rows': 3}))
    pages = forms.CharField(label='Страницы в файле', required=False, max_length=50)
    registry_url = forms.URLField(required=False, max_length=500, widget=forms.HiddenInput)
    registry_record_id = forms.CharField(required=False, max_length=100, widget=forms.HiddenInput)
    check_url = forms.URLField(required=False, max_length=500, widget=forms.HiddenInput)
    materials = forms.ModelMultipleChoiceField(queryset=Material.objects.all(), required=False)
    new_material = forms.BooleanField(label='Создать новые материалы', required=False)
    new_material_name = forms.CharField(label='Названия материалов — по одному на строку (как в актах)',
                                        required=False, widget=forms.Textarea(attrs={'rows': 2}))
    new_material_category = forms.CharField(label='Категория', required=False, max_length=200,
                                            widget=forms.TextInput(attrs={'list': 'material-categories'}))
    new_material_unit = forms.CharField(label='Ед. изм.', required=False, max_length=10)

    def __init__(self, *args, recognized_org=None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [('', '— не указан —')]
        org = recognized_org or {}
        if org.get('short_name') or org.get('inn'):
            label = org.get('short_name') or 'организация'
            if org.get('inn'):
                label += f" (ИНН {org['inn']})"
            choices.append(('new', f'+ Добавить в справочник: {label}'))
        else:
            choices.append(('new', '+ Новый контрагент'))
        choices += [(str(c.pk), f"{c.short_name} (ИНН {c.inn})" if c.inn else c.short_name)
                    for c in Counterparty.objects.order_by('short_name')]
        self.fields['manufacturer'].choices = choices

    def clean_new_inn(self):
        inn = re.sub(r'\D', '', self.cleaned_data.get('new_inn') or '')
        if inn and len(inn) not in (10, 12):
            raise forms.ValidationError('ИНН — 10 цифр (организация) или 12 (ИП).')
        return inn

    def clean(self):
        data = super().clean()
        kind = data.get('document_type')
        issue, expiry = data.get('issue_date'), data.get('expiry_date')
        if kind and Certificate.requires_expiry(kind) and not expiry:
            self.add_error('expiry_date', 'У сертификата и декларации должен быть срок действия.')
        if issue and expiry and expiry < issue:
            self.add_error('expiry_date', 'Срок действия раньше даты выдачи.')
        if data.get('manufacturer') == 'new' and not (data.get('new_short_name') or '').strip():
            self.add_error('new_short_name', 'Укажите название нового контрагента.')
        names = [n.strip() for n in (data.get('new_material_name') or '').splitlines() if n.strip()]
        data['new_material_names'] = names
        if data.get('new_material') and not names:
            self.add_error('new_material_name', 'Укажите название материала.')
        if any(len(n) > 200 for n in names):
            self.add_error('new_material_name', 'Название длиннее 200 знаков — сократите.')
        if not data.get('new_material') and not data.get('materials'):
            self.add_error(None, 'Отметьте материал из справочника или создайте новый — '
                                 'иначе документ не попадёт ни в один акт.')
        return data


_CUT = re.compile(r'\s*(?:;|\bГОСТ\b|\bТУ\b|\bСТО\b|Серийный выпуск|Партия|\bкод\b|изготовлен)', re.I)


def material_name_from_product(product):
    """Черновик названия материала из поля «Продукция»: до первого ГОСТа/ТУ/«Серийный выпуск»,
    не длиннее 120 знаков. Инженер правит под то, как материал пишут в актах."""
    text = re.sub(r'\s+', ' ', product or '').strip(' -—:')
    text = re.sub(r'^(?:с\s+)?код\w*\s+(?:ТН\s*ВЭД(?:\s*ЕАЭС)?|ОКПД\s*2?)\s*[—:–-]?\s*[\d\s]+', '', text, flags=re.I)
    if text.startswith('(') and ')' in text:
        text = text[1:text.index(')')]
    text = _CUT.split(text, maxsplit=1)[0].strip(' ,.-—:')
    if len(text) > 120:
        text = text[:120].rsplit(' ', 1)[0]
    return text[:1].upper() + text[1:]


def registry_found(doc):
    reg = doc.get('registry') or {}
    return reg.get('data') if reg.get('state') == 'found' else None


def _org_for(doc):
    """Изготовитель: из реестра, если документ там нашёлся, иначе со скана."""
    reg = registry_found(doc)
    if reg and (reg.get('manufacturer') or {}).get('short_name'):
        return reg['manufacturer']
    return doc.get('manufacturer') or {}


def _material_names(doc):
    """Черновики названий новых материалов: позиции продукции из реестра или поле «Продукция»."""
    reg = registry_found(doc)
    items = (reg or {}).get('product_items') or []
    names = [material_name_from_product(n) for n in items[:10]]
    if not names:
        names = [material_name_from_product((reg or {}).get('product') or doc.get('product'))]
    return '\n'.join(dict.fromkeys(n for n in names if n))


def registry_values(doc):
    """Поля карточки, которые берутся из реестра (поверх распознанного)."""
    reg = registry_found(doc)
    if not reg:
        return {}
    values = {'registry_url': reg.get('url') or '',
              'registry_record_id': reg.get('registry_record_id') or ''}
    for field, key in (('document_number', 'number'), ('issue_date', 'issue_date'),
                       ('expiry_date', 'expiry_date'), ('product_text', 'product'), ('issuer', 'issuer')):
        if reg.get(key):
            values[field] = reg[key]
    values['document_type'] = (Certificate.DocType.DECLARATION if reg.get('kind') == 'declaration'
                               else Certificate.DocType.CERTIFICATE)
    org = reg.get('manufacturer') or {}
    if org.get('short_name') or org.get('inn'):
        found = find_counterparty(org)
        values.update(manufacturer=str(found.pk) if found else 'new',
                      new_short_name=org.get('short_name', ''), new_full_name=org.get('full_name', ''),
                      new_inn=org.get('inn', ''), new_ogrn=org.get('ogrn', ''), new_address=org.get('address', ''))
    values['new_material_name'] = _material_names(doc)
    return values


def _initial(doc):
    org = _org_for(doc)
    found = find_counterparty(org)
    if found:
        manufacturer = str(found.pk)
    elif org.get('short_name') or org.get('inn'):
        manufacturer = 'new'
    else:
        manufacturer = ''
    return {
        'save_it': bool(doc.get('document_type')),
        'document_type': doc.get('document_type') or Certificate.DocType.CERTIFICATE,
        'document_number': doc.get('number') or '',
        'issue_date': doc.get('issue_date'),
        'expiry_date': doc.get('expiry_date'),
        'manufacturer': manufacturer,
        'new_short_name': org.get('short_name') or '',
        'new_full_name': org.get('full_name') or '',
        'new_inn': org.get('inn') or '',
        'new_ogrn': org.get('ogrn') or '',
        'new_address': org.get('address') or '',
        'issuer': doc.get('issuer') or '',
        'product_text': doc.get('product') or '',
        'pages': ', '.join(str(p) for p in doc.get('pages') or []),
        'registry_url': doc.get('registry_url') or '',
        'registry_record_id': doc.get('registry_record_id') or '',
        'check_url': doc.get('check_url') or '',
        'new_material_name': _material_names(doc),
    } | registry_values(doc), found


def _registry_source(reg):
    try:
        return Certificate.Source((reg or {}).get('provider') or Certificate.Source.REGISTRY)
    except ValueError:
        return Certificate.Source.REGISTRY


def _save_card(form, upload, doc):
    """Записывает одну карточку в базу. Возвращает сертификат."""
    d = form.cleaned_data
    reg = registry_found(doc)
    if reg and number_key(reg.get('number')) != number_key(d['document_number']):
        reg = None      # номер поменяли после проверки — данные реестра к нему не относятся

    manufacturer = None
    if d['manufacturer'] == 'new':
        inn = d['new_inn']
        manufacturer = Counterparty.objects.filter(inn=inn).first() if inn else None
        if manufacturer is None:
            manufacturer = Counterparty.objects.create(
                short_name=d['new_short_name'].strip(), full_name=d['new_full_name'].strip(),
                inn=inn, ogrn=d['new_ogrn'], address=d['new_address'], role='Производитель')
    elif d['manufacturer']:
        manufacturer = Counterparty.objects.get(pk=d['manufacturer'])

    cert = find_existing_certificate(d['document_number'])
    if cert is None:
        cert = Certificate(document_number=d['document_number'].strip())
    cert.document_type = d['document_type']
    cert.issue_date = d['issue_date']
    cert.expiry_date = d['expiry_date']
    cert.manufacturer = manufacturer or cert.manufacturer
    cert.document = upload.document
    cert.source = _registry_source(reg) if reg else Certificate.Source.DOCUMENT
    if reg:
        cert.registry_status = reg.get('status') or ''
        cert.registry_checked_at = _parse_dt((doc.get('registry') or {}).get('checked_at'))
    elif (doc.get('registry') or {}).get('state') == 'not_found':
        cert.registry_status = Certificate.RegistryStatus.NOT_FOUND
        cert.registry_checked_at = _parse_dt(doc['registry'].get('checked_at'))
    cert.verification_status = Certificate.Status.CONFIRMED
    cert.pages = d['pages']
    cert.product_text = d['product_text']
    cert.issuer = d['issuer']
    cert.registry_url = d['registry_url'] or cert.registry_url
    cert.registry_record_id = d['registry_record_id'] or cert.registry_record_id
    cert.check_url = d['check_url'] or cert.check_url
    cert.save()

    materials = list(d['materials'])
    if d['new_material']:
        standard = _standard_for(cert.product_text, (reg or {}).get('standards'))
        for name in d['new_material_names']:
            material = Material.objects.filter(name__iexact=name, manufacturer=cert.manufacturer).first()
            if material is None:
                material = Material.objects.create(
                    name=name, category=d['new_material_category'].strip(), unit=d['new_material_unit'].strip(),
                    manufacturer=cert.manufacturer, standard=standard)
            materials.append(material)
    for m in materials:
        MaterialCertificate.objects.get_or_create(material=m, certificate=cert)
    return cert


def _save_registry_certificate(number, result):
    reg = result['data']
    kind = (Certificate.DocType.DECLARATION if reg.get('kind') == 'declaration'
            else Certificate.DocType.CERTIFICATE)
    document_number = (reg.get('number') or number).strip()
    if not document_number or not reg.get('issue_date') or not reg.get('expiry_date'):
        raise ValueError('Реестр вернул неполные данные: не хватает номера или дат. Введите документ вручную.')

    org = reg.get('manufacturer') or {}
    manufacturer = find_counterparty(org)
    if manufacturer is None and (org.get('short_name') or org.get('inn')):
        manufacturer = Counterparty.objects.create(
            short_name=org.get('short_name') or org.get('full_name') or '',
            full_name=org.get('full_name') or '', inn=org.get('inn') or '',
            ogrn=org.get('ogrn') or '', address=org.get('address') or '', role='Производитель')

    cert = find_existing_certificate(document_number) or Certificate(document_number=document_number)
    cert.document_number = document_number
    cert.document_type = kind
    cert.issue_date = reg['issue_date']
    cert.expiry_date = reg['expiry_date']
    if manufacturer:
        cert.manufacturer = manufacturer
    cert.source = _registry_source(reg)
    cert.registry_status = reg.get('status') or ''
    cert.registry_url = reg.get('url') or ''
    cert.registry_record_id = reg.get('registry_record_id') or ''
    cert.registry_checked_at = _parse_dt(result.get('checked_at'))
    cert.verification_status = Certificate.Status.CONFIRMED
    cert.product_text = reg.get('product') or cert.product_text
    cert.issuer = reg.get('issuer') or cert.issuer
    cert.save()
    return cert


def _standard_for(text, registry_standards=None):
    """Норматив для нового материала. Из справочника — если он упомянут в документе;
    если документ найден в реестре и там указан ГОСТ, которого в справочнике нет, — добавляется."""
    from .models import RecordStatus, Standard
    key = _std_key(text) + ''.join(_std_key(s) for s in registry_standards or [])
    for st in Standard.objects.all():
        k = _std_key(st.designation)
        if k and k in key:
            return st
    gosts = [s for s in registry_standards or [] if s.upper().startswith('ГОСТ')]
    if gosts:
        return Standard.objects.create(designation=gosts[0], name=gosts[0], category='Из реестра',
                                       status=RecordStatus.ACTIVE)
    return None


def _parse_dt(value):
    from datetime import datetime
    from django.utils import timezone
    try:
        dt = datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


# ---------------------------------------------------------------------------
# Страницы
# ---------------------------------------------------------------------------

def _sha256(uploaded):
    sha = hashlib.sha256()
    for chunk in uploaded.chunks():
        sha.update(chunk)
    uploaded.seek(0)
    return sha.hexdigest()


def _registry_number_redirect(request, site=None):
    number = (request.POST.get('registry_number') or '').strip()
    material = Material.objects.filter(pk=request.POST.get('material') or 0).first()

    def back_to_uploads(result=None):
        if result and result.get('state') == 'candidates':
            request.session['registry_candidates'] = result
        else:
            request.session.pop('registry_candidates', None)
        params = {'registry_number': number} if number else {}
        if material:
            params['material'] = material.pk
        url = reverse('site_cert_uploads', args=[site.pk]) if site else reverse('cert_uploads')
        return redirect(f'{url}?{urlencode(params)}' if params else url)

    if not number:
        messages.error(request, 'Введите номер сертификата или декларации.')
        return back_to_uploads()

    if request.POST.get('action') == 'registry_choose':
        result = cert_jobs.check_in_registry({}, number=number)
        if result['state'] != 'candidates':
            messages.error(request, 'Не удалось повторно получить варианты. Выполните поиск ещё раз.')
            return back_to_uploads(result)
        try:
            candidate = result['candidates'][int(request.POST.get('registry_choice', '-1'))]
        except (ValueError, IndexError):
            messages.error(request, 'Выберите документ из списка.')
            return back_to_uploads(result)
        result = cert_jobs.check_in_registry(candidate, number=candidate['number'], allow_fuzzy=False)
    else:
        result = cert_jobs.check_in_registry({}, number=number)
        if result['state'] == 'candidates':
            messages.info(request, result['message'])
            return back_to_uploads(result)

    if result['state'] != 'found':
        messages.error(request, result['message'])
        return back_to_uploads(result)

    try:
        with transaction.atomic():
            cert = _save_registry_certificate(number, result)
            if material:
                MaterialCertificate.objects.get_or_create(material=material, certificate=cert)
    except ValueError as e:
        messages.error(request, str(e))
        return back_to_uploads()

    log_event(request, 'Документ о качестве загружен из реестра', cert, result['data'].get('url') or '')
    source_label = result['data'].get('source_label') or 'ФГИС Росаккредитации'
    text = f'{cert} сохранён по данным источника «{source_label}»'
    text += f' для материала «{material.name}».' if material else '.'
    if result['data'].get('status') == 'active':
        messages.success(request, text)
    else:
        messages.warning(request, text + f' Статус в реестре: {result["data"]["status_text"]}.')
    return redirect('ref_edit', slug='certificates', pk=cert.pk)


@login_required
def cert_uploads(request, site_pk=None):
    # В пространстве объекта загрузка запоминает происхождение; глобальная страница остаётся общей.
    site = ConstructionSite.objects.filter(pk=site_pk).first() if site_pk else None
    if site:
        set_current_site(request, site)

    if request.method == 'POST' and request.POST.get('action') in ('registry_number', 'registry_choose'):
        return _registry_number_redirect(request, site)

    if request.method == 'POST':
        files = request.FILES.getlist('files')
        if not files:
            messages.error(request, 'Выберите файл.')
            if site:
                return redirect('site_cert_uploads', site_pk=site.pk)
            return redirect('cert_uploads')

        material = Material.objects.filter(pk=request.POST.get('material') or 0).first()
        uploads = []
        for f in files:
            if not f.name.lower().endswith(ALLOWED_EXT):
                messages.error(request, f'«{f.name}»: поддерживаются PDF и картинки (JPG, PNG, TIFF).')
                continue
            if f.size > MAX_SIZE_MB * 1024 * 1024:
                messages.error(request, f'«{f.name}»: файл больше {MAX_SIZE_MB} МБ.')
                continue
            digest = _sha256(f)
            with transaction.atomic():
                doc = Documents.objects.filter(file_hash=digest).first()
                if doc:
                    earlier = CertificateUpload.objects.filter(document=doc).first()
                    if earlier:
                        messages.info(request, f'«{f.name}» уже загружали — открыта прошлая загрузка.')
                        if site and earlier.site_id is None:
                            # у загрузки ещё не было происхождения — запоминаем объект, откуда её открыли
                            earlier.site = site
                            earlier.save(update_fields=['site'])
                        uploads.append(earlier)
                        continue
                else:
                    doc = Documents.objects.create(path_to_file=f, file_hash=digest, document_type=UPLOAD_DOC_TYPE)
                upload = CertificateUpload.objects.create(document=doc, site=site)
                if material:
                    upload.result = {'material_id': material.pk}
                    upload.save(update_fields=['result'])
                cert_jobs.start(upload.pk)
                uploads.append(upload)

        if len(uploads) == 1:
            return redirect('cert_review', pk=uploads[0].pk)
        if uploads:
            messages.success(request, f'Файлов загружено: {len(uploads)}. Они распознаются по очереди — '
                                      'откройте каждый, чтобы проверить.')
        if site:
            return redirect('site_cert_uploads', site_pk=site.pk)
        return redirect('cert_uploads')

    material = Material.objects.filter(pk=request.GET.get('material') or 0).first()
    q = request.GET.get('q', '').strip().lower()
    uploads = CertificateUpload.objects.select_related('document')
    if site:
        uploads = uploads.filter(site=site)
    rows = []
    for up in uploads[:100]:
        name = os.path.basename(up.document.path_to_file.name)
        if q and q not in name.lower():
            continue
        code, text = cert_jobs.state(up)
        docs = up.result.get('documents', []) if code == 'done' else []
        rows.append({
            'upload': up,
            'name': name,
            'state': code,
            'state_text': text,
            'found': len(docs),
            'saved': len(up.result.get('saved', {})) if code == 'done' else 0,
        })
    from .cert_recognition import ocr_status
    ocr_ok, ocr_message = ocr_status(_tesseract_cmd())
    return render(request, 'core/cert_uploads.html', {
        'rows': rows, 'ocr_ok': ocr_ok, 'ocr_message': ocr_message, 'max_mb': MAX_SIZE_MB,
        'material': material, 'q': request.GET.get('q', '').strip(), 'site': site,
        'registry_number': request.GET.get('registry_number', '').strip(),
        'candidate_result': request.session.pop('registry_candidates', None),
    })


def _tesseract_cmd():
    from django.conf import settings
    return getattr(settings, 'TESSERACT_CMD', None)


def _card_context(upload, index, doc, form, suggestions):
    saved_id = upload.result.get('saved', {}).get(str(index))
    saved = Certificate.objects.filter(pk=saved_id).first() if saved_id else None
    number = form['document_number'].value()
    existing = None if saved else find_existing_certificate(number)
    kind = doc.get('document_type') or Certificate.DocType.CERTIFICATE
    selected_ids = {str(v) for v in (form['materials'].value() or [])}
    return {
        'index': index,
        'doc': doc,
        'form': form,
        'saved': saved,
        'existing': existing,
        'pages': doc.get('pages') or [],
        'fsa_search': '' if doc.get('voluntary') else FSA_SEARCH.get(kind, ''),
        'materials_html_ctx': _materials_ctx(upload, index, suggestions, selected_ids),
        'registry': doc.get('registry') or {},
        'can_check_registry': not saved and (Certificate.requires_expiry(form['document_type'].value())
                                             and not doc.get('voluntary')),
    }


def _materials_ctx(upload, index, suggestions, checked):
    return {
        'upload': upload,
        'index': index,
        'prefix': f'd{index}',
        'suggestions': suggestions,
        'groups': _material_groups(),
        'checked': checked,
    }


@login_required
def cert_review(request, pk):
    upload = get_object_or_404(CertificateUpload.objects.select_related('document', 'site'), pk=pk)
    code, state_text = cert_jobs.state(upload)
    file_name = os.path.basename(upload.document.path_to_file.name)

    if code != 'done':
        return render(request, 'core/cert_wait.html', {
            'upload': upload, 'state': code, 'state_text': state_text, 'file_name': file_name,
        })

    docs = upload.result.get('documents', [])
    saved_map = upload.result.setdefault('saved', {})
    data = request.POST if request.method == 'POST' else None
    quiet = False   # не показывать ошибки полей (страница перерисована после проверки в реестре)

    # «Проверить в реестре» у одной карточки: проверяем номер из формы, подставляем данные реестра
    recheck = request.POST.get('registry_check') if data else None
    if recheck is not None and recheck.isdigit() and int(recheck) < len(docs):
        i = int(recheck)
        number = (request.POST.get(f'd{i}-document_number') or '').strip()
        docs[i]['registry'] = cert_jobs.check_in_registry(docs[i], number=number)
        upload.save(update_fields=['result'])
        data = request.POST.copy()
        for field, value in registry_values(docs[i]).items():
            data[f'd{i}-{field}'] = value.isoformat() if hasattr(value, 'isoformat') else value
        quiet = True
        reg = docs[i]['registry']
        if reg['state'] == 'found':
            messages.success(request, f'Документ {i + 1}: найден в реестре, статус «{reg["data"]["status_text"]}». '
                                      'Поля заполнены по реестру.')
        elif reg['state'] == 'candidates':
            messages.info(request, f'Документ {i + 1}: {reg["message"]}')
        else:
            messages.error(request, f'Документ {i + 1}: {reg["message"]}')

    choice = request.POST.get('registry_choose') if data else None
    if choice and ':' in choice:
        card_index, candidate_index = choice.split(':', 1)
        i = int(card_index) if card_index.isdigit() else -1
        candidates = docs[i].get('registry', {}).get('candidates', []) if 0 <= i < len(docs) else []
        if candidate_index.isdigit() and int(candidate_index) < len(candidates):
            candidate = candidates[int(candidate_index)]
            docs[i]['registry'] = cert_jobs.check_in_registry(
                candidate, number=candidate['number'], allow_fuzzy=False)
            upload.save(update_fields=['result'])
            data = request.POST.copy()
            for field, value in registry_values(docs[i]).items():
                data[f'd{i}-{field}'] = value.isoformat() if hasattr(value, 'isoformat') else value
            quiet = True
            reg = docs[i]['registry']
            if reg['state'] == 'found':
                messages.success(request, f'Документ {i + 1}: выбранный вариант загружен из реестра.')
            else:
                messages.error(request, f'Документ {i + 1}: {reg["message"]}')

    for_material = Material.objects.filter(pk=upload.result.get('material_id') or 0).first()
    forms_ = []
    for i, doc in enumerate(docs):
        initial, found = _initial(doc)
        suggestions = suggest_materials(doc, found)
        # сразу отмечаем: материал, со страницы которого загружали, или уверенные подсказки;
        # если отмечать нечего — предлагаем создать новый материал
        if for_material:
            initial['materials'] = [for_material.pk]
        else:
            initial['materials'] = [m.pk for m, score, _ in suggestions if score >= 6]
        initial['new_material'] = not initial['materials']
        form = CertCardForm(data if str(i) not in saved_map else None, prefix=f'd{i}',
                            initial=initial, recognized_org=_org_for(doc))
        form.quiet = quiet
        forms_.append((i, doc, form, found, suggestions))

    if data is not None and not quiet:
        chosen = [(i, doc, f) for i, doc, f, found, sg in forms_ if f.is_bound and f.data.get(f'd{i}-save_it')]
        if not chosen:
            messages.error(request, 'Не отмечено ни одного документа для сохранения.')
        elif all(f.is_valid() for i, doc, f in chosen):
            with transaction.atomic():
                names = []
                for i, doc, f in chosen:
                    cert = _save_card(f, upload, doc)
                    saved_map[str(i)] = cert.pk
                    names.append(str(cert))
                upload.save(update_fields=['result'])
            log_event(request, 'Сохранены документы о качестве', upload.document, ', '.join(names))
            messages.success(request, 'Сохранено: ' + ', '.join(names) + '.')
            return redirect('cert_review', pk=upload.pk)
        else:
            messages.error(request, 'Не сохранено: проверьте поля, отмеченные красным.')

    cards = [_card_context(upload, i, doc, form, sg) for i, doc, form, found, sg in forms_]
    return render(request, 'core/cert_review.html', {
        'upload': upload,
        'file_name': file_name,
        'cards': cards,
        'warnings': upload.result.get('warnings', []),
        'pages_count': upload.result.get('pages_count', 0),
        'all_saved': cards and all(c['saved'] for c in cards),
        'unsaved': sum(1 for c in cards if not c['saved']),
        'for_material': for_material,
        'categories': sorted(set(Material.objects.exclude(category='').values_list('category', flat=True))),
    })


@login_required
def cert_materials(request, pk, index):
    """Блок «Материалы» одной карточки — заново, после добавления материала в отдельном окне."""
    upload = get_object_or_404(CertificateUpload, pk=pk)
    docs = upload.result.get('documents', [])
    if index >= len(docs):
        raise Http404
    doc = docs[index]
    manufacturer_id = request.GET.get('manufacturer', '')
    manufacturer = Counterparty.objects.filter(pk=manufacturer_id).first() if manufacturer_id.isdigit() else None
    ctx = _materials_ctx(upload, index, suggest_materials(doc, manufacturer), set(request.GET.getlist('checked')))
    return render(request, 'core/_cert_materials.html', ctx)


@login_required
def cert_status(request, pk):
    upload = get_object_or_404(CertificateUpload, pk=pk)
    code, text = cert_jobs.state(upload)
    return JsonResponse({'state': code, 'text': text})


@login_required
def cert_page(request, pk, n):
    upload = get_object_or_404(CertificateUpload, pk=pk)
    path = os.path.join(cert_jobs.preview_dir(upload.pk), f'page_{int(n)}.jpg')
    if not os.path.exists(path):
        raise Http404
    return FileResponse(open(path, 'rb'), content_type='image/jpeg')


@login_required
def cert_file(request, pk):
    upload = get_object_or_404(CertificateUpload.objects.select_related('document'), pk=pk)
    f = upload.document.path_to_file
    if not f or not os.path.exists(f.path):
        raise Http404
    return FileResponse(open(f.path, 'rb'), filename=os.path.basename(f.name))


@login_required
@require_POST
def cert_rerun(request, pk):
    upload = get_object_or_404(CertificateUpload, pk=pk)
    if upload.result.get('saved'):
        messages.error(request, 'Из этого файла уже сохранены документы — повторное распознавание отключено.')
        return redirect('cert_review', pk=pk)
    code, _ = cert_jobs.state(upload)
    if code in ('queued', 'processing'):
        return redirect('cert_review', pk=pk)
    upload.result = {k: v for k, v in upload.result.items() if k == 'material_id'}
    upload.save(update_fields=['result'])
    cert_jobs.start(upload.pk)
    return redirect('cert_review', pk=pk)


def _certificate_form_after_error(request, pk):
    request._cert_registry_preserve_form = True
    from .views import ref_edit
    return ref_edit(request, slug='certificates', pk=pk)


@login_required
@require_POST
def cert_registry_recheck(request, pk):
    """Кнопка «Проверить в реестре» на странице сохранённого сертификата."""
    cert = get_object_or_404(Certificate, pk=pk)
    if not cert_jobs.certificate_checkable(cert):
        messages.error(request, 'Отказные письма и добровольные сертификаты в реестре Росаккредитации не регистрируются.')
        return _certificate_form_after_error(request, pk)
    res = cert_jobs.recheck_certificate(cert)
    if res['state'] == 'found':
        text = f'В реестре: {res["data"]["status_text"]}.'
        if res.get('diffs'):
            text += ' Расхождения с базой: ' + '; '.join(f'{l}: у нас «{a}», в реестре «{b}»' for l, a, b in res['diffs']) + '.'
        if res['data'].get('status') == 'active' and not res.get('diffs'):
            messages.success(request, text)
            return redirect('ref_edit', slug='certificates', pk=pk)
        messages.error(request, text)
    else:
        messages.error(request, res['message'])
    return _certificate_form_after_error(request, pk)
