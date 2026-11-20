"""
core/registry.py

Список справочников для интерфейса. Чтобы добавить новый справочник —
добавьте строку сюда, отдельная страница не нужна.

Каждая запись:
    slug      — кусок адреса (/ref/<slug>/)
    model     — имя модели (можно несколько вариантов, берётся первая найденная)
    columns   — какие поля показывать в списке (несуществующие пропускаются)
    filters   — по каким полям (выбор / ссылка) можно фильтровать список
    search    — дополнительные пути ORM для строки поиска (обратные связи и прочее,
                что не покрывается текстовыми полями самой модели и её ссылок)
    inline    — (модель, поле-ссылка): строки, которые редактируются прямо внутри записи
"""

from django.apps import apps

GROUPS = [
    ('Объект', [
        dict(slug='sites', model=['ConstructionSite'],
             columns=['name', 'address', 'customer', 'general_contractor', 'designer'],
             filters=['customer', 'general_contractor', 'designer']),
        dict(slug='sections', model=['DocumentationSection'],
             columns=['order_number', 'name', 'project_code', 'site', 'status'],
             filters=['site', 'status', 'standard'],
             search=['worktype__name']),
        dict(slug='worktypes', model=['WorkType'],
             columns=['order_number', 'name', 'unit', 'section', 'standard', 'status'],
             filters=['section', 'status', 'standard', 'requires_hidden_work_act']),
        dict(slug='rooms', model=['Room'],
             columns=['project_number', 'explication_name', 'floor', 'building', 'site'],
             filters=['site', 'building', 'floor']),
        dict(slug='estimate', model=['EstimateLine'],
             columns=['room', 'work_type', 'estimated_volume', 'estimate_code', 'ks2_position'],
             filters=['work_type', 'room']),
    ]),
    ('Организации и люди', [
        dict(slug='counterparties', model=['Counterparty'],
             columns=['short_name', 'inn', 'role', 'status'], filters=['status', 'role']),
        dict(slug='sro', model=['SRO'],
             columns=['organization', 'name', 'code', 'issue_date', 'status'],
             filters=['organization', 'status']),
        dict(slug='persons', model=['Person'],
             columns=['last_name', 'first_name', 'position', 'organization',
                      'order_document', 'authority_end_date', 'status'],
             filters=['organization', 'status', 'position']),
    ]),
    ('Материалы и сертификаты', [
        dict(slug='standards', model=['Standard'],
             columns=['designation', 'name', 'category', 'status'], filters=['status', 'category']),
        dict(slug='materials', model=['Material'],
             columns=['name', 'unit', 'standard', 'manufacturer', 'status'],
             filters=['manufacturer', 'status', 'category', 'standard'],
             search=['materialcertificate__certificate__document_number']),
        dict(slug='certificates', model=['Certificate'],
             columns=['document_number', 'document_type', 'materials', 'manufacturer', 'issue_date',
                      'expiry_date', 'verification_status', 'source'],
             filters=['document_type', 'manufacturer', 'verification_status', 'source', 'registry_status'],
             search=['materialcertificate__material__name'],
             prefetch=['materialcertificate_set__material'],
             inline=('MaterialCertificate', 'certificate')),
        dict(slug='documents', model=['Documents', 'Document'],
             columns=['document_type', 'path_to_file', 'uploaded_at'],
             filters=['document_type']),
    ]),
    ('Поставки', [
        dict(slug='deliveries', model=['DeliveryNote'],
             columns=['number', 'delivery_date', 'supplier', 'site', 'acceptance_status', 'document'],
             filters=['site', 'supplier', 'acceptance_status', 'received_by', 'has_quality_passport'],
             search=['deliveryitem__original_text', 'deliveryitem__material__name'],
             inline=('DeliveryItem', 'delivery_note')),
        dict(slug='inspections', model=['IncomingInspection'],
             columns=['inspection_date', 'delivery_item', 'control_type', 'inspection_result',
                      'inspector_name', 'inspector', 'usage_permission'],
             filters=['inspector', 'control_type', 'inspection_result', 'usage_permission']),
    ]),
    ('Работы', [
        dict(slug='volumes', model=['WorkVolume'],
             columns=['performance_date', 'work_type', 'room', 'actual_volume', 'unit', 'act', 'acceptance_status'],
             filters=['act', 'work_type', 'room', 'acceptance_status', 'performed_by']),
        dict(slug='deviations', model=['DeviationResolution'],
             columns=['estimate_line', 'deviation_amount', 'decision_date', 'status'],
             filters=['status', 'prepared_by']),
    ]),
    ('Служебное', [
        dict(slug='aliases', model=['MaterialAlias'], columns=['text', 'material'], filters=['material']),
        dict(slug='events', model=['EventLog'], columns=['created_at', 'action', 'object_repr', 'user', 'details'],
             filters=['user', 'action']),
    ]),
]


def _resolve(names):
    for name in names:
        try:
            return apps.get_model('core', name)
        except LookupError:
            continue
    return None


def get_entries():
    """Все справочники, у которых модель реально существует, с подставленными классами моделей."""
    result = []
    for group_title, items in GROUPS:
        group = []
        for item in items:
            model = _resolve(item['model'])
            if model is None:
                continue
            entry = dict(item, model=model)
            if item.get('inline'):
                child = _resolve([item['inline'][0]])
                entry['inline'] = (child, item['inline'][1]) if child else None
            group.append(entry)
        if group:
            result.append((group_title, group))
    return result


def get_entry(slug):
    for _, group in get_entries():
        for entry in group:
            if entry['slug'] == slug:
                return entry
    return None
