"""Раскладка длинных форм справочников по смысловым блокам.

Формы создаются динамически из моделей, поэтому список полей задаётся здесь:
важное остаётся открытым, редкое — в свёрнутом блоке. Поля, которых нет в
раскладке, не теряются — они попадают в блок «Прочее».
"""

LAYOUTS = {
    'certificates': [
        {
            'key': 'main', 'title': 'Документ', 'open': True,
            'fields': ('document_type', 'document_number', 'issue_date',
                       'expiry_date', 'verification_status', 'source', 'manufacturer'),
        },
        {
            'key': 'product', 'title': 'Продукция и скан', 'open': True,
            'fields': ('product_text', 'pages', 'document'),
        },
        {
            'key': 'verify', 'title': 'Данные реестра', 'open': False,
            'fields': ('registry_status', 'registry_url', 'registry_record_id',
                       'check_url', 'registry_checked_at', 'issuer', 'sheet_count'),
        },
    ],
    'persons': [
        {
            'key': 'main', 'title': 'Участник', 'open': True,
            'fields': ('organization', 'last_name', 'first_name', 'middle_name',
                       'position', 'role', 'status'),
        },
        {
            'key': 'authority', 'title': 'Полномочия', 'open': False,
            'fields': ('order_document', 'order_document_file',
                       'authority_end_date', 'nrs_id'),
        },
    ],
    'deliveries': [
        {
            'key': 'main', 'title': 'Накладная', 'open': True,
            'fields': ('number', 'delivery_date', 'site', 'supplier',
                       'has_quality_passport', 'document'),
        },
        {
            'key': 'accept', 'title': 'Приёмка', 'open': False,
            'fields': ('received_by', 'acceptance_status', 'notes'),
        },
    ],
    'counterparties': [
        {
            'key': 'main', 'title': 'Организация', 'open': True,
            'fields': ('short_name', 'full_name', 'inn', 'role', 'status'),
        },
        {
            'key': 'requisites', 'title': 'Реквизиты', 'open': False,
            'fields': ('ogrn', 'kpp', 'address', 'contacts'),
        },
    ],
    'sites': [
        {
            'key': 'main', 'title': 'Объект', 'open': True,
            'fields': ('name', 'address', 'customer', 'general_contractor',
                       'designer', 'code'),
        },
        {
            'key': 'docs', 'title': 'Документация и сроки', 'open': False,
            'fields': ('contract_reference', 'design_doc_reference',
                       'construction_start', 'planned_completion', 'actual_completion'),
        },
        {
            'key': 'registry', 'title': 'Реестр к АОСР', 'open': False,
            'fields': ('responsible_person', 'registry_submitted_by',
                       'registry_accepted_by', 'registry_approved_by'),
        },
    ],
}


def grouped_fields(form, slug):
    """Список блоков формы: [{title, open, fields, has_errors}].

    None — для формы нет раскладки или блок получился один: тогда используется
    обычный _form.html без лишней обёртки.
    """
    layout = LAYOUTS.get(slug)
    if not layout:
        return None

    visible = list(form.visible_fields())
    by_name = {field.name: field for field in visible}
    seen = set()
    groups = []

    for spec in layout:
        fields = [
            by_name[name]
            for name in spec['fields']
            if name in by_name and name not in seen
        ]
        if not fields:
            continue
        seen.update(field.name for field in fields)
        groups.append({
            'key': spec['key'],
            'title': spec['title'],
            'open': spec.get('open', False),
            'fields': fields,
            'has_errors': any(field.errors for field in fields),
        })

    rest = [field for field in visible if field.name not in seen]
    if rest:
        groups.append({
            'key': 'other', 'title': 'Прочее', 'open': True,
            'fields': rest, 'has_errors': any(field.errors for field in rest),
        })

    return groups if len(groups) > 1 else None
