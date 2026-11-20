"""Точный предпросмотр акта через общий LibreOffice-конвертер.

Работает с несохранённым состоянием формы, ничего не записывает в базу.
Кэш: media/generated_acts/preview/act_<id>/<sha1 содержимого формы>/p1.png…
Если LibreOffice недоступен, вызывающий код показывает HTML-превью.
"""

import hashlib
import os
import re
import shutil

import pypdfium2 as pdfium
from django.conf import settings

from .office import OfficeConversionError, convert_xlsx_to_pdf

PREVIEW_DIR = os.path.join(settings.MEDIA_ROOT, 'generated_acts', 'preview')


class PreviewUnavailable(Exception):
    """Конвертация недоступна — нужен запасной HTML-вариант."""


def cache_key(act_id, data):
    """Ключ кэша по содержимому формы (без csrf-токена). None — сохранённый акт."""
    if data is None:
        payload = 'saved'
    else:
        payload = sorted((k, tuple(sorted(data.getlist(k)))) for k in data if k != 'csrfmiddlewaretoken')
    return hashlib.sha1(repr((act_id, payload)).encode('utf-8')).hexdigest()


def preview_dir(act_id, key):
    return os.path.join(PREVIEW_DIR, f'act_{act_id}', key)


def build_preview_pages(act_id, key, context, header):
    """Страницы PNG для точного предпросмотра; кэш по key. Возвращает имена файлов p1.png…"""
    from .act_export import render_act_workbook

    folder = preview_dir(act_id, key)
    pages = sorted(f for f in os.listdir(folder) if re.fullmatch(r'p\d+\.png', f)) \
        if os.path.isdir(folder) else []
    if pages:
        return pages

    os.makedirs(folder, exist_ok=True)
    xlsx_path = os.path.join(folder, 'act.xlsx')
    pdf_path = os.path.join(folder, 'act.pdf')
    try:
        wb = render_act_workbook(context, header)
        wb.save(xlsx_path)
        convert_xlsx_to_pdf(xlsx_path, pdf_path)
        pdf = pdfium.PdfDocument(pdf_path)
        try:
            for i in range(len(pdf)):
                pdf[i].render(scale=1.6).to_pil().save(os.path.join(folder, f'p{i + 1}.png'))
        finally:
            pdf.close()
    except (OSError, OfficeConversionError) as e:
        shutil.rmtree(folder, ignore_errors=True)
        raise PreviewUnavailable(str(e)[:300]) from e

    for sibling in os.listdir(os.path.dirname(folder)):
        if sibling != key:
            shutil.rmtree(os.path.join(os.path.dirname(folder), sibling), ignore_errors=True)
    return sorted(f for f in os.listdir(folder) if re.fullmatch(r'p\d+\.png', f))
