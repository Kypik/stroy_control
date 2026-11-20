import io
import logging
import os
import re
import tempfile

from django.conf import settings
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .models import ActAttachment
from .office import OfficeConversionError, convert_xlsx_to_pdf
from .services import REGISTRY_DOCUMENT_TYPE

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(settings.MEDIA_ROOT, 'generated_acts')
TEMP_DIR = os.path.join(OUTPUT_DIR, 'tmp')


def _convert_act_to_pdf(act, folder):
    from .act_export import generate_act_excel

    excel_path = generate_act_excel(act.pk)
    output_path = os.path.join(folder, os.path.splitext(os.path.basename(excel_path))[0] + '.pdf')
    return convert_xlsx_to_pdf(excel_path, output_path)


def _append_pdf(writer, readers, source):
    reader = PdfReader(source)
    if reader.is_encrypted:
        reader.decrypt('')
    for page in reader.pages:
        writer.add_page(page)
    readers.append(reader)
    return len(reader.pages)


def _append_image(writer, readers, path):
    image = ImageReader(path)
    width, height = image.getSize()
    page_size = landscape(A4) if width > height else A4
    page_width, page_height = page_size
    margin = 18
    scale = min((page_width - 2 * margin) / width, (page_height - 2 * margin) / height)
    draw_width, draw_height = width * scale, height * scale

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=page_size)
    pdf.drawImage(
        image, (page_width - draw_width) / 2, (page_height - draw_height) / 2,
        width=draw_width, height=draw_height, preserveAspectRatio=True)
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    _append_pdf(writer, readers, buffer)


def _document_path(document):
    try:
        path = document.path_to_file.path
    except (ValueError, NotImplementedError):
        return None
    return path if os.path.isfile(path) else None


def build_final_act_pdf(act):
    warnings = []
    writer = PdfWriter()
    readers = []
    os.makedirs(TEMP_DIR, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix='act_pdf_', dir=TEMP_DIR) as folder:
            next_page = 1
            try:
                act_pdf = _convert_act_to_pdf(act, folder)
                body_pages = _append_pdf(writer, readers, act_pdf)
                next_page = body_pages + 1
            except OfficeConversionError as e:
                logger.warning('Не удалось преобразовать акт %s в PDF: %s', act.pk, e)
                warnings.append(
                    'Тело акта не добавлено: не удалось преобразовать Excel в PDF. '
                    'Проверьте установку LibreOffice.')

            attachments = list(ActAttachment.objects.filter(act=act).select_related('document').order_by('pk'))
            registries = [a for a in attachments
                          if a.document.document_type.casefold() == REGISTRY_DOCUMENT_TYPE.casefold()]
            others = [a for a in attachments if a not in registries]
            for attachment in registries + others:
                document = attachment.document
                if attachment in registries:
                    from .registry_export import save_act_registry
                    path, _ = save_act_registry(
                        act, auto_generated=document.auto_generated, start_page=next_page)
                    next_page += _append_pdf(writer, readers, path)
                    continue
                path = _document_path(document)
                if not path:
                    warnings.append(f'Файл приложения не найден и пропущен: {document.path_to_file.name}')
                    continue
                extension = os.path.splitext(path)[1].lower()
                try:
                    if extension == '.pdf':
                        _append_pdf(writer, readers, path)
                    elif extension in ('.jpg', '.jpeg', '.png'):
                        _append_image(writer, readers, path)
                except Exception:
                    logger.exception('Не удалось добавить приложение %s к акту %s', document.pk, act.pk)
                    warnings.append(
                        f'Приложение «{document.document_type}» пропущено: '
                        'файл повреждён или имеет неподдерживаемый формат.')

            safe_number = re.sub(r'[\\/:*?"<>|]+', '-', act.act_number)
            output_path = os.path.join(OUTPUT_DIR, f'АОСР_№{safe_number}_итоговый.pdf')
            writer.add_metadata({'/Title': f'АОСР № {act.act_number} с приложениями'})
            with open(output_path, 'wb') as output:
                writer.write(output)
    finally:
        for reader in readers:
            try:
                reader.stream.close()
            except Exception:
                pass
        try:
            os.rmdir(TEMP_DIR)
        except OSError:
            pass

    return output_path, warnings
