from django.db import migrations, models


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

HEADER_TEXT_FIELDS = (
    'object_name', 'object_address',
    'customer_name', 'customer_inn', 'customer_ogrn', 'customer_address',
    'contractor_name', 'contractor_inn', 'contractor_ogrn', 'contractor_address',
    'contractor_sro_name', 'contractor_sro_code',
    'designer_name', 'designer_inn', 'designer_ogrn', 'designer_address',
    'designer_sro_name', 'designer_sro_code',
    'design_doc_reference',
)


def _org_short_name(org):
    """Копия core.services.org_short_name для исторических моделей."""
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
    return {'name': sro.name, 'code': sro.code, 'date': sro.issue_date.isoformat() if sro.issue_date else ''}


def _build_snapshot(site, SRO):
    contractor_sro = _sro_fields(
        SRO.objects.filter(organization=site.general_contractor, status='active').first())
    designer_sro = _sro_fields(
        SRO.objects.filter(organization=site.designer, status='active').first())
    return {
        'object_name': site.name or '',
        'object_address': site.address or '',
        'customer_name': _org_short_name(site.customer),
        'customer_inn': site.customer.inn or '',
        'customer_ogrn': site.customer.ogrn or '',
        'customer_address': site.customer.address or '',
        'contractor_name': _org_short_name(site.general_contractor),
        'contractor_inn': site.general_contractor.inn or '',
        'contractor_ogrn': site.general_contractor.ogrn or '',
        'contractor_address': site.general_contractor.address or '',
        'contractor_sro_name': contractor_sro['name'],
        'contractor_sro_code': contractor_sro['code'],
        'contractor_sro_date': contractor_sro['date'],
        'designer_name': _org_short_name(site.designer),
        'designer_inn': site.designer.inn or '',
        'designer_ogrn': site.designer.ogrn or '',
        'designer_address': site.designer.address or '',
        'designer_sro_name': designer_sro['name'],
        'designer_sro_code': designer_sro['code'],
        'designer_sro_date': designer_sro['date'],
        'design_doc_reference': site.design_doc_reference or '',
    }


def fill_header_snapshots(apps, schema_editor):
    """Существующим актам — снимок из текущих данных объекта.
    Старое переопределение реквизитов документации переносится в общую схему."""
    HiddenWorkAct = apps.get_model('core', 'HiddenWorkAct')
    SRO = apps.get_model('core', 'SRO')

    acts = HiddenWorkAct.objects.select_related(
        'section__site__customer', 'section__site__general_contractor', 'section__site__designer',
    ).iterator()
    for act in acts:
        site = act.section.site
        snapshot = _build_snapshot(site, SRO)
        override = (act.design_doc_reference_override or '').strip()
        if override:
            snapshot['design_doc_reference'] = override
        HiddenWorkAct.objects.filter(pk=act.pk).update(header_snapshot=snapshot)


def clear_header_snapshots(apps, schema_editor):
    HiddenWorkAct = apps.get_model('core', 'HiddenWorkAct')
    HiddenWorkAct.objects.update(header_snapshot={})


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0028_certificateupload_site'),
    ]

    operations = [
        migrations.AddField(
            model_name='hiddenworkact',
            name='header_snapshot',
            field=models.JSONField(blank=True, default=dict, verbose_name='Шапка акта (снимок)'),
        ),
        migrations.RunPython(fill_header_snapshots, clear_header_snapshots),
        migrations.RemoveField(
            model_name='hiddenworkact',
            name='design_doc_reference_override',
        ),
    ]
