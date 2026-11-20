from datetime import datetime

from .models import (
    ConstructionSite,
    HiddenWorkAct,
    WorkVolume,
    ActMaterial,
    ActSigner,
    ActAttachment,
    MaterialCertificate,
    Certificate,
)

REGISTRY_DOCUMENT_TYPE = 'Реестр к АОСР'

# Организационно-правовые формы и их сокращения (длинные варианты — раньше коротких)
LEGAL_FORMS = [
    ('Федеральное государственное унитарное предприятие', 'ФГУП'),
    ('Государственное унитарное предприятие', 'ГУП'),
    ('Муниципальное унитарное предприятие', 'МУП'),
    ('Государственное бюджетное учреждение', 'ГБУ'),
    ('Государственное казённое учреждение', 'ГКУ'),
    ('Государственное казенное учреждение', 'ГКУ'),
    ('Общество с ограниченной ответственностью', 'ООО'),
    ('Публичное акционерное общество', 'ПАО'),
    ('Непубличное акционерное общество', 'АО'),
    ('Закрытое акционерное общество', 'ЗАО'),
    ('Открытое акционерное общество', 'ОАО'),
    ('Акционерное общество', 'АО'),
    ('Индивидуальный предприниматель', 'ИП'),
]


def org_short_name(org):
    """Краткое имя организации для акта: «ООО «Ромашка»», а не полная расшифровка.
    Берётся краткое наименование, если его нет — полное; форма собственности сокращается в любом случае."""
    if not org:
        return ''
    name = (org.short_name or org.full_name or '').strip()
    lowered = name.lower()
    for full, short in LEGAL_FORMS:
        if lowered.startswith(full.lower()):
            name = short + name[len(full):]
            break
    return name


def _sro_fields(sro):
    if not sro:
        return {'name': '', 'code': '', 'date': ''}
    return {
        'name': sro.name,
        'code': sro.code,
        'date': sro.issue_date,
    }
 
 
def build_header_context(site_id):
    site = ConstructionSite.objects.select_related(
        'customer', 'general_contractor', 'designer', 'responsible_person'
    ).get(id=site_id)
 
    contractor_sro = _sro_fields(
        site.general_contractor.sro_set.filter(status='active').first()
    )
    designer_sro = _sro_fields(
        site.designer.sro_set.filter(status='active').first()
    )
 
    return {
        'object_name': site.name,
        'object_address': site.address,
 
        'customer_name': org_short_name(site.customer),
        'customer_inn': site.customer.inn,
        'customer_ogrn': site.customer.ogrn,
        'customer_address': site.customer.address,
 
        'contractor_name': org_short_name(site.general_contractor),
        'contractor_inn': site.general_contractor.inn,
        'contractor_ogrn': site.general_contractor.ogrn,
        'contractor_address': site.general_contractor.address,
        'contractor_sro_name': contractor_sro['name'],
        'contractor_sro_code': contractor_sro['code'],
        'contractor_sro_date': contractor_sro['date'],
 
        'designer_name': org_short_name(site.designer),
        'designer_inn': site.designer.inn,
        'designer_ogrn': site.designer.ogrn,
        'designer_address': site.designer.address,
        'designer_sro_name': designer_sro['name'],
        'designer_sro_code': designer_sro['code'],
        'designer_sro_date': designer_sro['date'],
 
        'design_doc_reference': site.design_doc_reference,
    }
 
# Шапка акта: поля снимка и подписи для скрытого блока в карточке.
# Порядок и названия — как в бланке АОСР.
HEADER_FIELDS = (
    ('object_name', 'Объект капитального строительства'),
    ('object_address', 'Адрес объекта'),
    ('customer_name', 'Застройщик (технический заказчик)'),
    ('customer_inn', 'ИНН застройщика'),
    ('customer_ogrn', 'ОГРН застройщика'),
    ('customer_address', 'Адрес застройщика'),
    ('contractor_name', 'Лицо, осуществляющее строительство'),
    ('contractor_inn', 'ИНН подрядчика'),
    ('contractor_ogrn', 'ОГРН подрядчика'),
    ('contractor_address', 'Адрес подрядчика'),
    ('contractor_sro_name', 'СРО подрядчика'),
    ('contractor_sro_code', 'СРО подрядчика — номер'),
    ('contractor_sro_date', 'СРО подрядчика — дата выдачи'),
    ('designer_name', 'Лицо, осуществляющее подготовку проектной документации'),
    ('designer_inn', 'ИНН проектировщика'),
    ('designer_ogrn', 'ОГРН проектировщика'),
    ('designer_address', 'Адрес проектировщика'),
    ('designer_sro_name', 'СРО проектировщика'),
    ('designer_sro_code', 'СРО проектировщика — номер'),
    ('designer_sro_date', 'СРО проектировщика — дата выдачи'),
    ('design_doc_reference', 'Реквизиты проектной документации'),
)

HEADER_DATE_FIELDS = ('contractor_sro_date', 'designer_sro_date')

_HEADER_FORMATS = ('%d.%m.%Y', '%d.%m.%y', '%Y-%m-%d')


def _header_date(value):
    """Дата из снимка/формы: принимает date, ISO-строку и дд.мм.гггг; пусто — None."""
    if not value:
        return None
    if hasattr(value, 'strftime'):
        return value.date() if isinstance(value, datetime) else value
    text = str(value).strip()
    for fmt in _HEADER_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def header_to_snapshot(header):
    """Шапка -> JSON для HiddenWorkAct.header_snapshot. Даты — строками ISO."""
    snapshot = {}
    for name, _label in HEADER_FIELDS:
        value = header.get(name)
        if name in HEADER_DATE_FIELDS:
            snapshot[name] = value.isoformat() if hasattr(value, 'isoformat') else (value or '')
        else:
            snapshot[name] = '' if value is None else str(value)
    return snapshot


def snapshot_to_header(snapshot):
    """JSON снимка -> шапка с теми же ключами, что у build_header_context."""
    header = {}
    for name, _label in HEADER_FIELDS:
        value = (snapshot or {}).get(name, '')
        header[name] = _header_date(value) if name in HEADER_DATE_FIELDS else str(value or '')
    return header


def header_for_act(act):
    """Шапка конкретного акта: снимок, сделанный при создании/правке.
    У старых актов снимка нет — собираем из текущей карточки объекта."""
    if act.header_snapshot:
        return snapshot_to_header(act.header_snapshot)
    return build_header_context(act.section.site_id)


def header_overrides_from_post(post):
    """Правки из скрытого блока. None — блока в запросе нет, шапку не трогаем."""
    if not any(f'header_{name}' in post for name, _label in HEADER_FIELDS):
        return None
    overrides = {}
    for name, _label in HEADER_FIELDS:
        value = (post.get(f'header_{name}') or '').strip()
        overrides[name] = _header_date(value) if name in HEADER_DATE_FIELDS else value
    return overrides


def header_snapshot_from_post(base_header, post):
    """Снимок с учётом правок формы. None — правок не было."""
    overrides = header_overrides_from_post(post)
    if overrides is None:
        return None
    merged = dict(base_header)
    merged.update(overrides)
    return header_to_snapshot(merged)


def header_rows(header):
    """Строки для шаблона скрытого блока: подпись, значение для показа и для поля."""
    rows = []
    for name, label in HEADER_FIELDS:
        value = header.get(name)
        if name in HEADER_DATE_FIELDS and hasattr(value, 'strftime'):
            text = value.strftime('%d.%m.%Y')
        else:
            text = str(value or '')
        rows.append({'name': name, 'label': label, 'value': text, 'display': text})
    return rows


# Шапка акта в карточке показывается смысловыми блоками, а не плоским списком из 21 строки.
HEADER_GROUPS = (
    ('object', 'Объект', ('object_name', 'object_address')),
    ('customer', 'Застройщик', ('customer_name', 'customer_inn', 'customer_ogrn',
                                'customer_address')),
    ('contractor', 'Лицо, осуществляющее строительство', (
        'contractor_name', 'contractor_inn', 'contractor_ogrn', 'contractor_address',
        'contractor_sro_name', 'contractor_sro_code', 'contractor_sro_date')),
    ('designer', 'Проектировщик', (
        'designer_name', 'designer_inn', 'designer_ogrn', 'designer_address',
        'designer_sro_name', 'designer_sro_code', 'designer_sro_date')),
    ('design_doc', 'Проектная документация', ('design_doc_reference',)),
)


def header_row_groups(header):
    """Те же строки, что header_rows, но сгруппированные для свёрнутого блока карточки."""
    rows = {row['name']: row for row in header_rows(header)}
    return [
        {'key': key, 'title': title, 'rows': [rows[name] for name in names if name in rows]}
        for key, title, names in HEADER_GROUPS
    ]


def header_summary(header):
    """Короткая сводка для summary шапки: что именно зафиксировано в акте."""
    return [
        ('Объект', header.get('object_name') or ''),
        ('Застройщик', header.get('customer_name') or ''),
        ('Подрядчик', header.get('contractor_name') or ''),
        ('Проектировщик', header.get('designer_name') or ''),
    ]


def _format_person_name(person):
    """Иванов И.И. — фамилия и инициалы, без лишней точки, если нет отчества."""
    first_initial = f"{person.first_name[0]}." if person.first_name else ""
    middle_initial = f"{person.middle_name[0]}." if person.middle_name else ""
    return f"{person.last_name} {first_initial}{middle_initial}".strip()
 
 
def certificate_text(cert):
    """Как документ о качестве записывается в акт."""
    kind = cert.document_type or 'Сертификат соответствия'
    if kind == Certificate.DocType.REFUSAL:
        return f"{kind} №{cert.document_number} от {cert.issue_date:%d.%m.%Y}"
    if cert.expiry_date:
        return f"{kind} №{cert.document_number} с {cert.issue_date:%d.%m.%Y} по {cert.expiry_date:%d.%m.%Y}"
    return f"{kind} №{cert.document_number} от {cert.issue_date:%d.%m.%Y}"


def _valid_on(cert, day):
    """Действовал ли документ в этот день. Документы без обязательного срока бессрочные."""
    if not Certificate.requires_expiry(cert.document_type):
        return True
    if day is None:
        return True
    if cert.issue_date and cert.issue_date > day:
        return False
    return bool(cert.expiry_date) and cert.expiry_date >= day


def pick_certificate(material, day):
    """Документ о качестве материала, действовавший на дату поставки или начала работ.
    Возвращает (документ или None, текст проблемы или None)."""
    certs = [mc.certificate for mc in MaterialCertificate.objects.filter(material=material)
             .select_related('certificate')]
    certs = [c for c in certs if c.verification_status != Certificate.Status.REJECTED]
    if not certs:
        return None, 'Нет сертификата'

    fitting = [c for c in certs if _valid_on(c, day)]
    if not fitting:
        latest = max(certs, key=lambda c: c.issue_date)
        period = (f"с {latest.issue_date:%d.%m.%Y} по {latest.expiry_date:%d.%m.%Y}"
                  if latest.expiry_date else "без срока действия")
        return latest, (f"{latest.document_type} №{latest.document_number} не действовал на дату поставки "
                        f"{day:%d.%m.%Y} ({period})")

    # сначала подтверждённые, среди них — самые свежие
    fitting.sort(key=lambda c: (c.verification_status == Certificate.Status.CONFIRMED, c.issue_date), reverse=True)
    cert = fitting[0]
    if cert.registry_status in (Certificate.RegistryStatus.ENDED, Certificate.RegistryStatus.SUSPENDED):
        return cert, f"{cert.document_type} №{cert.document_number} {cert.get_registry_status_display()} в реестре"
    if cert.source != Certificate.Source.MANUAL and cert.verification_status != Certificate.Status.CONFIRMED:
        return cert, f"{cert.document_type} №{cert.document_number} распознан автоматически и не проверен"
    return cert, None


def get_materials_for_act(act, act_materials=None):
    if act_materials is None:
        act_materials = ActMaterial.objects.filter(act=act).order_by('id').select_related(
            'delivery_item__material', 'delivery_item__delivery_note', 'material'
        )

    first_work = WorkVolume.objects.filter(act=act, performance_date__isnull=False) \
        .order_by('performance_date').values_list('performance_date', flat=True).first()
    direct_certificate_day = first_work or act.drawn_up_date

    materials_list = []
    for am in act_materials:
        item = am.delivery_item
        material = am.material if am.material_id else (item.material if item else None)
        if material is None:
            if item is None:
                continue
            materials_list.append({
                'name': item.original_text,
                'quantity': am.written_off_volume,
                'unit': item.unit,
                'certificate_text': "⚠ МАТЕРИАЛ НЕ СОПОСТАВЛЕН",
                'certificate_problem': 'Позиция накладной не сопоставлена с материалом из справочника',
            })
            continue

        certificate_day = item.delivery_note.delivery_date if item else direct_certificate_day
        cert, problem = pick_certificate(material, certificate_day)
        if problem:
            cert_text = f"⚠ {problem.upper()}"
        else:
            cert_text = certificate_text(cert)

        materials_list.append({
            'name': material.name,
            'quantity': am.written_off_volume,
            'unit': item.unit if item else material.unit,
            'certificate_text': cert_text,
            'certificate_problem': problem,   # None — всё в порядке
        })

    return materials_list


def blocking_problems(act_context):
    """Проблемы с документами о качестве — источник текстов предупреждений.
    Excel формируется всегда; в файле такие места отмечены «⚠»."""
    return [
        f"{m['name']}: {m['certificate_problem'][0].lower()}{m['certificate_problem'][1:]}"
        for m in act_context['materials'] if m.get('certificate_problem')
    ]


def get_signers_for_act(act, signers=None):
    if signers is None:
        signers = ActSigner.objects.filter(act=act).select_related(
            'person', 'person__organization'
        )
 
    result = {}
    for signer in signers:
        person = signer.person
        org = person.organization
 
        result[signer.role_in_act] = {
            'position': person.position,
            'full_name': _format_person_name(person),
            'order_document': person.order_document,
            'nrs_id': person.nrs_id,
            'org_short_name': org_short_name(org),
            'org_inn': org.inn if org else '',
            'org_ogrn': org.ogrn if org else '',
            'org_address': org.address if org else '',
        }
 
    return result

def get_attachments_for_act(act, materials, act_attachments=None):
    """Перечень приложений: сначала документы о качестве материалов, потом файлы-приложения.
    Возвращает (список, сколько первых пунктов — документы о качестве) — это нужно,
    чтобы при выносе материалов в приложение их сертификаты из перечня убрать."""
    attachments = []

    seen_certificates = set()
    for m in materials:
        cert_text = m['certificate_text']
        if cert_text and cert_text not in seen_certificates:
            attachments.append(cert_text)
            seen_certificates.add(cert_text)
    cert_count = len(attachments)

    if act_attachments is None:
        act_attachments = ActAttachment.objects.filter(act=act).order_by('pk').select_related('document')
    for aa in act_attachments:
        attachments.append(aa.document.document_type)

    return attachments, cert_count
 
def get_standards_text_for_act(act, volumes):
    from .models import Standard

    standards = [Standard.objects.filter(designation='СП 48.13330.2019').first(), act.section.standard]
    standards += [v.work_type.standard for v in volumes if v.work_type_id]
    seen = set()
    parts = []
    for standard in standards:
        if not standard or not standard.designation:
            continue
        designation = standard.designation.strip()
        if designation not in seen:
            seen.add(designation)
            parts.append(designation)
    return ', '.join(parts)

def get_next_work_text(act):
    if act.next_work_override:
        return act.next_work_override
    if act.next_work_type:
        return act.next_work_type.name
    return ''
 
def _assemble_act_context(act, volumes, act_materials=None, signers=None, act_attachments=None):
    """Общая сборка контекста акта. Списки можно передать из несохранённых объектов —
    так работает предпросмотр текущего состояния формы без записи в базу."""

    work_descriptions = []
    for v in volumes:
        # замер может быть черновым: собираем из того, что заполнено
        name = v.work_type.name if v.work_type_id else ''
        text = ' '.join(p for p in (name, v.location.strip()) if p)
        if text:
            work_descriptions.append(text)
    work_description_text = "; ".join(work_descriptions)

    dates = [v.performance_date for v in volumes if v.performance_date]
    end_dates = [v.end_date or v.performance_date for v in volumes if v.end_date or v.performance_date]
    start_date = min(dates) if dates else None
    end_date = max(end_dates) if end_dates else None
 
    materials = get_materials_for_act(act, act_materials)
    signers = get_signers_for_act(act, signers)
    attachments, cert_attachments = get_attachments_for_act(act, materials, act_attachments)
    has_registry = any(text.strip().casefold() == REGISTRY_DOCUMENT_TYPE.casefold() for text in attachments)
    standards_text = get_standards_text_for_act(act, volumes)
    next_work_text = get_next_work_text(act)
 
    return {
        'act_id': act.id,
        'act_number': act.act_number,
        'drawn_up_date': act.drawn_up_date,
        'site_id': act.section.site_id,
        'work_description': work_description_text,
        'work_start_date': start_date,
        'work_end_date': end_date,
        'materials': materials,
        'signers': signers,
        'attachments': attachments,
        'cert_attachments': cert_attachments,
        'has_registry': has_registry,
        'standards_text': standards_text,
        'next_work_text': next_work_text,
        'copies_count': act.copies_count,
    }
 

def build_act_context(act_id):
    act = HiddenWorkAct.objects.select_related(
        'section', 'section__site', 'next_work_type'
    ).get(id=act_id)
    volumes = WorkVolume.objects.filter(act=act).select_related('work_type', 'room')
    return _assemble_act_context(act, volumes)


def build_act_context_preview(act, volumes, act_materials, signers, act_attachments):
    """Контекст из несохранённых данных формы (для живого предпросмотра)."""
    return _assemble_act_context(act, volumes, act_materials, signers, act_attachments)
