import html
import os
import re
from pathlib import Path

from django.conf import settings
from django.db import transaction
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import ActAttachment, ActMaterial, ActSigner, Certificate, Documents, Person, RecordStatus
from .pdf_fonts import FONT_BOLD, FONT_REGULAR, register_pdf_fonts
from .services import REGISTRY_DOCUMENT_TYPE, _format_person_name, header_for_act, org_short_name

REGISTRY_OUTPUT_DIR = os.path.join(settings.MEDIA_ROOT, 'generated_acts', 'registries')


def _register_font():
    register_pdf_fonts()


def _format_date(value):
    return value.strftime('%d.%m.%Y') if value else ''


def _certificate_date(certificate):
    issue = _format_date(certificate.issue_date)
    if certificate.document_type in (Certificate.DocType.CERTIFICATE, Certificate.DocType.DECLARATION):
        expiry = _format_date(certificate.expiry_date)
        return f'с {issue} по {expiry}' if expiry else f'с {issue}'
    return issue


def _certificate_organization(certificate):
    return (certificate.issuer or '').strip() or org_short_name(certificate.manufacturer)


def _row(certificate, material=None):
    name_parts = [certificate.document_type]
    if material is not None:
        name_parts.append(material.name)
    elif certificate.product_text:
        name_parts.append(certificate.product_text.strip())
    return {
        'name': ' '.join(part for part in name_parts if part),
        'number': certificate.document_number,
        'date': _certificate_date(certificate),
        'organization': _certificate_organization(certificate),
        'sheet_count': certificate.sheet_count,
    }


def build_registry_rows(act, start_page=1):
    rows = []
    seen_material_rows = set()
    seen_certificates = set()
    act_materials = ActMaterial.objects.filter(act=act).select_related(
        'material', 'delivery_item__material',
    ).prefetch_related(
        'material__materialcertificate_set__certificate__manufacturer',
        'delivery_item__material__materialcertificate_set__certificate__manufacturer',
    ).order_by('pk')

    for act_material in act_materials:
        material = act_material.material or (
            act_material.delivery_item.material if act_material.delivery_item else None)
        if material is None:
            continue
        for link in material.materialcertificate_set.all():
            certificate = link.certificate
            key = (material.pk, certificate.pk)
            if key in seen_material_rows:
                continue
            seen_material_rows.add(key)
            seen_certificates.add(certificate.pk)
            rows.append(_row(certificate, material))

    protocols = act.protocols.filter(
        document_type=Certificate.DocType.TEST_PROTOCOL,
    ).select_related('manufacturer').order_by('pk')
    for certificate in protocols:
        if certificate.pk not in seen_certificates:
            rows.append(_row(certificate))
            seen_certificates.add(certificate.pk)

    running = start_page
    for number, row in enumerate(rows, start=1):
        row['number_pp'] = number
        sheet_count = row['sheet_count']
        if sheet_count:
            row['pages'] = str(running) if sheet_count == 1 else f'{running}-{running + sheet_count - 1}'
            running += sheet_count
        else:
            row['pages'] = ''
    return rows


def _paragraph(value, style):
    text = html.escape(str(value or '')).replace('\n', '<br/>')
    return Paragraph(text or '&nbsp;', style)


def _fallback_person(site, field_name, organization):
    person = getattr(site, field_name)
    if person:
        return person
    return Person.objects.filter(
        organization=organization, status=RecordStatus.ACTIVE,
    ).order_by('pk').first()


def generate_registry_pdf(act, output_path, start_page=1):
    _register_font()
    site = act.section.site
    title = f'Реестр к АОСР {act.act_number} от {_format_date(act.drawn_up_date)} г.'
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    regular = ParagraphStyle(
        'RegistryCell', fontName=FONT_REGULAR, fontSize=8, leading=10,
        alignment=TA_LEFT, spaceAfter=0, spaceBefore=0)
    centered = ParagraphStyle('RegistryCellCenter', parent=regular, alignment=TA_CENTER)
    header = ParagraphStyle(
        'RegistryHeader', parent=centered, fontSize=8, leading=9.5)
    title_style = ParagraphStyle(
        'RegistryTitle', fontName=FONT_BOLD, fontSize=12, leading=14,
        alignment=TA_CENTER, spaceAfter=0)
    signature = ParagraphStyle(
        'RegistrySignature', fontName=FONT_REGULAR, fontSize=9, leading=12,
        alignment=TA_LEFT)
    signature_name = ParagraphStyle(
        'RegistrySignatureName', parent=signature,
        fontName=FONT_BOLD, alignment=TA_RIGHT)

    data = [[
        _paragraph(text, header) for text in (
            '№\nп/п', 'Наименование документа', '№ акта/схемы', 'Дата\nдокумента',
            'Организация\nсоставившая\nдокумент', 'Кол-во\nлистов', 'Страница\nпо списку',
        )
    ]]
    for row in build_registry_rows(act, start_page=start_page):
        values = (
            row['number_pp'], row['name'], row['number'], row['date'],
            row['organization'], row['sheet_count'] or '', row['pages'],
        )
        data.append([
            _paragraph(value, regular if index == 1 else centered)
            for index, value in enumerate(values)
        ])

    available_width = A4[0] - 14 * mm
    table = Table(data, colWidths=[28, 185, 92, 70, 90, 40, available_width - 505], repeatRows=1)
    table.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))

    story = [Spacer(1, 3 * mm), _paragraph(title, title_style), Spacer(1, 9 * mm), table]
    builder_signer = ActSigner.objects.filter(
        act=act, role_in_act=ActSigner.Role.CONTRACTOR,
    ).select_related('person__organization').first()
    builder_organization = (builder_signer.person.organization if builder_signer
                            else site.general_contractor)
    # организация в подписях — из снимка шапки акта, а не из текущей карточки объекта
    contractor_label = (header_for_act(act).get('contractor_name')
                        or org_short_name(site.general_contractor))
    signature_rows = (
        ('Сдал на проверку', contractor_label, _fallback_person(
            site, 'registry_submitted_by', site.general_contractor)),
        ('Принял на проверку', contractor_label, _fallback_person(
            site, 'registry_accepted_by', builder_organization)),
        ('Согласовал', contractor_label, _fallback_person(
            site, 'registry_approved_by', builder_organization)),
    )
    for label, organization_label, person in signature_rows:
        left = _paragraph(f'{label}\n{organization_label}', signature)
        right = _paragraph(_format_person_name(person) if person else '', signature_name)
        signature_table = Table([[left, right]], colWidths=[available_width * 0.65, available_width * 0.35])
        signature_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 25),
            ('RIGHTPADDING', (0, 0), (-1, -1), 25),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.extend([Spacer(1, 5 * mm), signature_table])

    document = SimpleDocTemplate(
        output_path, pagesize=A4, leftMargin=7 * mm, rightMargin=7 * mm,
        topMargin=10 * mm, bottomMargin=15 * mm, title=title)
    document.build(story)
    return output_path


def save_act_registry(act, auto_generated=False, start_page=1):
    if auto_generated:
        manual = ActAttachment.objects.filter(
            act=act, document__document_type__iexact=REGISTRY_DOCUMENT_TYPE,
            document__auto_generated=False,
        ).select_related('document').first()
        if manual:
            return manual.document.path_to_file.path, manual.document
        attachment = ActAttachment.objects.filter(
            act=act, document__document_type__iexact=REGISTRY_DOCUMENT_TYPE,
            document__auto_generated=True,
        ).select_related('document').first()
    else:
        attachment = ActAttachment.objects.filter(
            act=act, document__document_type__iexact=REGISTRY_DOCUMENT_TYPE,
        ).select_related('document').order_by('document__auto_generated', 'pk').first()

    safe_number = re.sub(r'[\\/:*?"<>|]+', '-', act.act_number)
    filename = f'Реестр_к_АОСР_№{safe_number}.pdf'
    output_path = os.path.join(REGISTRY_OUTPUT_DIR, filename)
    generate_registry_pdf(act, output_path, start_page=start_page)
    relative_path = Path(output_path).relative_to(settings.MEDIA_ROOT).as_posix()

    with transaction.atomic():
        if attachment:
            document = attachment.document
            update_fields = []
            if document.path_to_file.name != relative_path:
                document.path_to_file.name = relative_path
                update_fields.append('path_to_file')
            if document.auto_generated != auto_generated:
                document.auto_generated = auto_generated
                update_fields.append('auto_generated')
            if update_fields:
                document.save(update_fields=update_fields)
        else:
            document = Documents.objects.create(
                path_to_file=relative_path, document_type=REGISTRY_DOCUMENT_TYPE,
                auto_generated=auto_generated)
            ActAttachment.objects.create(act=act, document=document)
    return output_path, document


def delete_auto_act_registry(act):
    attachments = list(ActAttachment.objects.filter(
        act=act, document__document_type__iexact=REGISTRY_DOCUMENT_TYPE,
        document__auto_generated=True,
    ).select_related('document'))
    for attachment in attachments:
        document = attachment.document
        attachment.delete()
        if not ActAttachment.objects.filter(document=document).exists():
            storage = document.path_to_file.storage
            name = document.path_to_file.name
            document.delete()
            if name:
                storage.delete(name)
    return len(attachments)


def sync_act_registry(act):
    from .act_export import act_needs_registry

    if act_needs_registry(act):
        save_act_registry(act, auto_generated=True)
        return True
    delete_auto_act_registry(act)
    return False
