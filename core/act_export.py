import os
import re
from copy import copy

import openpyxl
from django.conf import settings
from openpyxl.worksheet.cell_range import CellRange, MultiCellRange
from openpyxl.worksheet.merge import MergedCellRange

from .models import HiddenWorkAct
from .services import header_for_act, build_act_context

TEMPLATE_PATH = os.path.join(settings.BASE_DIR, 'core', 'templates_xlsx', 'Шаблон_АОСР.xlsx')
SHEET_NAME = '1'
OUTPUT_DIR = os.path.join(settings.MEDIA_ROOT, 'generated_acts')

RU_MONTHS = {
    1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
    5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
    9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря',
}

# Строки ВЫШЕ блока материалов — не сдвигаются никогда
SIGNER_TOP_ROWS = {
    'customer':           (34, 35),
    'customer_control':   (38, 39),
    'contractor':         (42, 43),
    'contractor_control': (47, 48),
    'designer':           (51, 53),
    'work_performer':     (56, 57),
}

# Строки НИЖЕ блоков материалов и приложений — номера из пустого шаблона,
# при записи к ним прибавляется число вставленных строк
SIGNER_BOTTOM_ROWS = {
    'customer': 109,
    'customer_control': 112,
    'contractor': 115,
    'contractor_control': 118,
    'designer': 121,
    'work_performer': 124,
}

# Блок описания работ: в шаблоне строки 71–73, ниже них подпись-пояснение (74)
WORK_FIRST_ROW = 71

# Блок материалов: в шаблоне строки 81–83, ниже них подпись-пояснение (84)
MATERIALS_FIRST_ROW = 81

# Блок приложений: в шаблоне строки 104–106 с номерами « 1.» « 2.» « 3.» в колонке B
ATTACHMENTS_FIRST_ROW = 104


def _format_date_parts(d):
    if not d:
        return ('', '', '')
    return (f"{d.day:02d}", RU_MONTHS[d.month], f"{d.year} г.")


def _format_date(d):
    return d.strftime('%d.%m.%Y') if d else ''


# ---------------------------------------------------------------------------
# Вставка строк с сохранением вёрстки бланка.
# Стандартный ws.insert_rows двигает только значения и стили ячеек,
# но НЕ двигает объединённые ячейки, высоты строк и область печати —
# без этого всё ниже вставки «разъезжается».
# ---------------------------------------------------------------------------

def _insert_rows_like(ws, at, amount, source_row):
    """Вставляет amount строк перед строкой at, оформляя их как source_row (source_row < at)."""
    if amount <= 0:
        return

    # свойства строк ниже вставки (высота, скрытость, стиль строки) — переносим целиком
    moved = {}
    for r, dim in list(ws.row_dimensions.items()):
        if r >= at:
            moved[r] = (dim.height, dim.hidden, dim.outlineLevel, dim._style)
    source_merges = [mr for mr in ws.merged_cells.ranges if mr.min_row == mr.max_row == source_row]

    ws.insert_rows(at, amount)

    # объединённые ячейки ниже вставки — сдвигаем, пересекающие её — растягиваем
    shifted = []
    for mr in ws.merged_cells.ranges:
        min_row, max_row = mr.min_row, mr.max_row
        if min_row >= at:
            min_row += amount
            max_row += amount
        elif max_row >= at:
            max_row += amount
        shifted.append(CellRange(min_col=mr.min_col, min_row=min_row, max_col=mr.max_col, max_row=max_row).coord)
    ws.merged_cells = MultiCellRange()
    for coord in shifted:
        ws.merged_cells.add(MergedCellRange(ws, coord))

    for r in moved:
        del ws.row_dimensions[r]
    for r, (height, hidden, outline, style) in moved.items():
        dim = ws.row_dimensions[r + amount]
        dim.height, dim.hidden, dim.outlineLevel = height, hidden, outline
        dim._style = copy(style)

    # новые строки — как строка-образец: стили, высота, объединения
    for r in range(at, at + amount):
        for col in range(1, ws.max_column + 1):
            src = ws.cell(row=source_row, column=col)
            dst = ws.cell(row=r, column=col)
            if src.has_style:
                dst._style = copy(src._style)
        ws.row_dimensions[r].height = ws.row_dimensions[source_row].height
        ws.row_dimensions[r].hidden = False
        for mr in source_merges:
            ws.merge_cells(start_row=r, start_column=mr.min_col, end_row=r, end_column=mr.max_col)


# Ручной разрыв ставится на границе разделов, а печать идёт в фиксированном
# масштабе 64% (как в шаблоне) с полями 0.5" сверху/снизу — так оригинал.
# Подписанты остаются цельно на стр. 2; страница вмещает ~1200 пунктов строк.
_PAGE_BUDGET_PT = 1180.0

_SECTION_STARTS = ('произвели осмотр работ', ' 1. К освидетельствованию', ' 2. Работы выполнены',
                   ' 3. При выполнении', ' 4. Предъявлены', ' 5. Даты:',
                   ' 6. Работы выполнены в соответствии', ' 7. Разрешается',
                   'Дополнительные сведения', 'Акт составлен в',
                   'Представитель застройщика или заказчика,')


def _apply_page_break(ws):
    from openpyxl.worksheet.pagebreak import Break, RowBreak
    from openpyxl.worksheet.properties import PageSetupProperties

    ws.row_breaks = RowBreak()
    boundaries = []
    preferred = []
    for row in ws.iter_rows(min_col=2, max_col=2):
        cell = row[0]
        text = str(cell.value or '')
        if any(text.startswith(p) for p in _SECTION_STARTS):
            boundaries.append(cell.row)
        if text.startswith(' 2. Работы выполнены'):
            preferred.append(cell.row)

    def height(r):
        if ws.row_dimensions[r].hidden:
            return 0.0
        return ws.row_dimensions[r].height or 14.25

    total = 0.0
    cum_before = {}
    for r in range(1, ws.max_row + 1):
        cum_before[r] = total
        total += height(r)

    fitting = [b for b in boundaries
               if cum_before[b] <= _PAGE_BUDGET_PT and total - cum_before[b] <= _PAGE_BUDGET_PT]
    candidates = preferred or fitting or boundaries
    if candidates:
        split = candidates[0] if preferred else min(
            candidates, key=lambda b: abs(cum_before[b] - total / 2))
        ws.row_breaks.append(Break(id=split - 1))

    if ws.sheet_properties.pageSetUpPr is None:
        from openpyxl.worksheet.properties import PageSetupProperties
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=False)
    else:
        ws.sheet_properties.pageSetUpPr.fitToPage = False
    # Фиксированный масштаб, НЕ вписывание: иначе LibreOffice игнорирует ручной
    # разрыв и рвёт разделы (заголовок без значения). Ровно 2 страницы
    # обеспечивают бюджет высоты + ручной разрыв.
    ws.page_setup.fitToWidth = None
    ws.page_setup.fitToHeight = None
    ws.page_setup.scale = 64
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = ws.ORIENTATION_PORTRAIT
    ws.page_margins.top = 0.5
    ws.page_margins.bottom = 0.5


def _trim_sheet(ws):
    """Обрезает пустой хвост шаблона (стили без содержимого до строки ~1000 и колонки правее T)
    и ставит область печати по фактическому содержимому — иначе при печати выходят пустые страницы."""
    last = 0
    for row in ws.iter_rows():
        for cell in row:
            if cell.value not in (None, ''):
                last = max(last, cell.row)
    last = max(last, max((mr.max_row for mr in ws.merged_cells.ranges), default=0))
    if ws.max_column > 20:
        ws.delete_cols(21, ws.max_column - 20)
    if ws.max_row > last:
        ws.delete_rows(last + 1, ws.max_row - last)
    ws.print_area = f'B1:T{last}'



# ---------------------------------------------------------------------------
# Перенос длинного текста на несколько строк бланка.
# Excel сам не переносит текст в объединённой строке — хвост просто не печатается.
# Сколько символов влезает, считается по ширине колонок и размеру шрифта:
# откалибровано по бланку (строка B:T шрифтом 13 вмещает ~118 символов), с запасом.
# ---------------------------------------------------------------------------

def _line_capacity(ws, row, first_col, last_col):
    from openpyxl.utils import get_column_letter
    width = sum((ws.column_dimensions[get_column_letter(c)].width or 8.43)
                for c in range(first_col, last_col + 1))
    size = ws.cell(row=row, column=first_col).font.sz or 11
    return max(20, int(width * 9.9 / size * 0.93))


# --- Точное измерение: ширина слота и текста в пикселях (PIL + настоящий TTF) ---
from openpyxl.utils import get_column_letter

_FONT_CACHE = {}


def _measure_font(cell):
    """Bundled TTF для измерения текста; None — используем эвристику."""
    from PIL import ImageFont
    from .pdf_fonts import FONT_DIR

    size_px = max(6, int(round((cell.font.sz or 11) * 96 / 72)))
    key = (cell.font.name, size_px, bool(cell.font.b), bool(cell.font.i))
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    filename = 'DejaVuSans-Bold.ttf' if cell.font.b else 'DejaVuSans.ttf'
    try:
        font = ImageFont.truetype(str(FONT_DIR / filename), size_px)
    except OSError:
        font = None
    _FONT_CACHE[key] = font
    return font


def _slot_width_px(ws, row, first_col, last_col):
    """Ширина слота в пикселях по формуле Excel: px = round(width * 7) + 5 на колонку."""
    width = 0
    for c in range(first_col, last_col + 1):
        w = ws.column_dimensions[get_column_letter(c)].width or 8.43
        width += int(round(w * 7)) + 5
    return max(50, width - 8)   # минус внутренние отступы ячейки


def _wrap_px(text, max_px, font):
    """Перенос по словам по измеренной ширине строки, а не по числу символов."""
    if font is None:
        size_pt = 11
        return _wrap(text, max(20, int(max_px * 1.75 / (size_pt * 96 / 72))))
    words = str(text).split()
    lines, current = [], ''
    for word in words:
        while font.getlength(word) > max_px:            # слово длиннее строки — режем по ширине
            if current:
                lines.append(current)
                current = ''
            cut = word
            while len(cut) > 1 and font.getlength(cut) > max_px:
                cut = cut[:-1]
            lines.append(cut)
            word = word[len(cut):]
        candidate = f"{current} {word}" if current else word
        if font.getlength(candidate) <= max_px:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or ['']


def _flow_lines_px(text, widths_px, font):
    """Перетекание по слотам разной ширины: строки только там, где текст реально не влезает."""
    if font is None:
        caps = [max(20, int(w * 1.75 / (11 * 96 / 72))) for w in widths_px]
        return _flow_lines(text, caps)
    if not widths_px:
        widths_px = [10 ** 9]

    def cap(n):
        return widths_px[min(n, len(widths_px) - 1)]

    words = str(text).split()
    lines, current = [], ''
    for word in words:
        while font.getlength(word) > cap(len(lines)):
            if current:
                lines.append(current)
                current = ''
            c = cap(len(lines))
            cut = word
            while len(cut) > 1 and font.getlength(cut) > c:
                cut = cut[:-1]
            lines.append(cut)
            word = word[len(cut):]
        candidate = f"{current} {word}" if current else word
        if font.getlength(candidate) <= cap(len(lines)):
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or ['']


def _flow_lines(text, capacities):
    """Разбивает текст на строки бланка: первая вмещает capacities[0] символов,
    вторая — capacities[1] и т.д.; если строк больше, чем слотов, вместимость —
    как у последнего слота (новые строки вставляются по его образцу)."""
    if not capacities:
        capacities = [10 ** 9]

    def cap(n):
        return capacities[min(n, len(capacities) - 1)]

    words = str(text).split()
    lines, current = [], ''
    for word in words:
        while len(word) > cap(len(lines)):   # слово длиннее строки — режем как есть
            if current:
                lines.append(current)
                current = ''
            c = cap(len(lines))
            lines.append(word[:c])
            word = word[c:]
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= cap(len(lines)):
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or ['']


def _org_line(name, ogrn, inn):
    """«ООО «Ромашка», ОГРН …, ИНН …» — без висячих «ОГРН ,» при пустых реквизитах."""
    parts = [p for p in (name, f'ОГРН {ogrn}' if ogrn else '', f'ИНН {inn}' if inn else '') if p]
    return ', '.join(parts)


def _fit_merged_rows(ws):
    """Финальный проход: ни одна строка бланка не должна обрезать текст.
    Если содержимое объединённой строки не влезает в одну линию — включаем перенос
    и поднимаем высоту. Ловит и подписи-пояснения самого шаблона, и данные.
    Ширина слота учитывает свободные ячейки справа (подписи перетекают в них
    при печати), а текст измеряется с поправкой 0.85: шаблон в Times New Roman,
    измеряем по DejaVu (шире) — без поправки однострочные подписи раздуваются
    в две строки и лист перестаёт влезать в 2 страницы."""
    tnr_factor = 0.85
    for mr in ws.merged_cells.ranges:
        if mr.min_row != mr.max_row:
            continue
        cell = ws.cell(row=mr.min_row, column=mr.min_col)
        text = str(cell.value or '')
        if not text.strip():
            continue
        font = _measure_font(cell)
        # подписи перетекают в пустые ячейки справа — учитываем это в ширине
        last = mr.max_col
        for c in range(mr.max_col + 1, 21):
            if str(ws.cell(row=mr.min_row, column=c).value or '').strip():
                break
            last = c
        if font is not None:
            slot_px = _slot_width_px(ws, mr.min_row, mr.min_col, last)
            lines = len(_wrap_px(text, int(slot_px / tnr_factor), font))
        else:
            lines = len(_wrap(text, _line_capacity(ws, mr.min_row, mr.min_col, last)))
        if lines <= 1:
            continue
        if not cell.alignment.wrap_text:
            alignment = copy(cell.alignment)
            alignment.wrap_text = True
            cell.alignment = alignment
        needed = 3 + lines * (cell.font.sz or 11) * 1.3
        dim = ws.row_dimensions[mr.min_row]
        if not dim.height or dim.height < needed:
            dim.height = needed


def _wrap(text, limit):
    """Разбивает текст на строки не длиннее limit, по пробелам."""
    words = str(text).split()
    lines, current = [], ''
    for word in words:
        while len(word) > limit:            # слово длиннее строки — режем как есть
            if current:
                lines.append(current)
                current = ''
            lines.append(word[:limit])
            word = word[limit:]
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= limit:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or ['']


def _fmt_qty(value):
    """120.00 → «120», 120.50 → «120.5» — без хвостовых нулей."""
    if value is None:
        return ''
    if hasattr(value, 'normalize'):
        return format(value.normalize(), 'f')
    return str(value)


def material_names(materials):
    return list(dict.fromkeys(m['name'] for m in materials if m.get('name')))


def _material_lines(materials, width, font=None):
    """Материал и сертификат в одну строку, а если не влезает — как в примере заполнения:
    сначала материал с количеством, сертификат с новой строки.
    width — пиксели, если задан font (PIL), иначе символы (старая эвристика)."""
    lines = []
    for m in materials:
        head = f"{m['name']} {_fmt_qty(m['quantity'])} {m['unit']}."
        full = f"{head} {m['certificate_text']}"
        fits = font.getlength(full) <= width if font else len(full) <= width
        if fits:
            lines.append(full)
        elif font:
            lines += _wrap_px(head, width, font) + _wrap_px(m['certificate_text'], width, font)
        else:
            lines += _wrap(head, width) + _wrap(m['certificate_text'], width)
    return lines


# Бланк рассчитан на 2 страницы. Строк материалов больше этого числа не влезает —
# тогда в акте пишем, что они в приложении. Число откалибровано печатью реальных актов.
MAX_MATERIAL_LINES_IN_ACT = 7       # откалибровано печатью: 7 строк ещё дают ровно 2 страницы
APPENDIX_NOTE = 'материалы и документы о качестве указаны в приложении к настоящему акту.'
APPENDIX_ATTACHMENT_NOTE = 'Перечень применённых материалов и документов о качестве — в приложении к акту'


_DEFAULT_MEASURE = None


def _default_measure():
    """Ширина полной строки B:T и шрифт материалов — для проверки приложения без открытия книги."""
    global _DEFAULT_MEASURE
    if _DEFAULT_MEASURE is None:
        wb = openpyxl.load_workbook(TEMPLATE_PATH)
        ws = wb[SHEET_NAME]
        _DEFAULT_MEASURE = (_slot_width_px(ws, MATERIALS_FIRST_ROW, 2, 20),
                            _measure_font(ws.cell(row=MATERIALS_FIRST_ROW, column=2)))
    return _DEFAULT_MEASURE


def _material_line_count(materials):
    width, font = _default_measure()
    if font is None:
        return len(_material_lines(materials, 98))
    return len(_material_lines(materials, width, font))


def materials_need_appendix(materials):
    """Нужно ли приложение: материалов слишком много, в двухстраничный акт они не влезают."""
    return _material_line_count(materials) > MAX_MATERIAL_LINES_IN_ACT


def _attachment_sheet_count(document):
    try:
        path = document.path_to_file.path
    except (ValueError, NotImplementedError):
        return 1
    if os.path.splitext(path)[1].lower() == '.pdf' and os.path.isfile(path):
        try:
            from pypdf import PdfReader
            with open(path, 'rb') as source:
                return max(1, len(PdfReader(source).pages))
        except Exception:
            pass
    return 1


def act_needs_registry(act):
    """Переполнение по строкам материалов плюс листы обычных приложений акта."""
    from .models import ActAttachment
    from .services import REGISTRY_DOCUMENT_TYPE, build_act_context

    context = build_act_context(act.pk)
    attachments = ActAttachment.objects.filter(act=act).exclude(
        document__document_type__iexact=REGISTRY_DOCUMENT_TYPE,
    ).select_related('document')
    attachment_sheets = sum(_attachment_sheet_count(a.document) for a in attachments)
    return _material_line_count(context['materials']) + attachment_sheets > MAX_MATERIAL_LINES_IN_ACT


def render_act_workbook(act, header):
    """Готовая книга Excel по контексту акта и шапке объекта.
    Контекст может быть собран из несохранённой формы (живой предпросмотр) —
    подстановки и вёрстка при этом те же, что у скачиваемого файла."""
    wb = openpyxl.load_workbook(TEMPLATE_PATH)
    ws = wb[SHEET_NAME]

    # --- Раскладываем длинные тексты по строкам (ширина слотов и текста — по пустому шаблону) ---
    work_lines = []
    work_font = _measure_font(ws.cell(row=WORK_FIRST_ROW, column=2))
    work_width = _slot_width_px(ws, WORK_FIRST_ROW, 2, 20)
    for part in [p for p in act['work_description'].split('; ') if p]:
        if work_font:
            work_lines += _wrap_px(part + ';', work_width, work_font)
        else:
            work_lines += _wrap(part + ';', _line_capacity(ws, WORK_FIRST_ROW, 2, 20))
    if work_lines:
        work_lines[-1] = work_lines[-1].rstrip(';')

    mat_font = _measure_font(ws.cell(row=MATERIALS_FIRST_ROW, column=2))
    mat_width = (_slot_width_px(ws, MATERIALS_FIRST_ROW, 2, 20) if mat_font
                 else _line_capacity(ws, MATERIALS_FIRST_ROW, 2, 20))
    material_lines = _material_lines(act['materials'], mat_width, mat_font)
    # При наличии реестра в акте остаются уникальные материалы, а документы качества
    # заменяются ссылкой на реестр. Без реестра действует прежний предел строк.
    materials_in_registry = act.get('has_registry', False)
    materials_in_appendix = not materials_in_registry and len(material_lines) > MAX_MATERIAL_LINES_IN_ACT
    if materials_in_registry:
        names = material_names(act['materials'])
        names_text = '; '.join(names) + '.' if names else ''
        registry_note = (f"Перечень документов о качестве приведён в реестре к АОСР № {act['act_number']} "
                         f"от {_format_date(act['drawn_up_date'])}")
        if mat_font:
            material_lines = (_wrap_px(names_text, mat_width, mat_font) if names_text else [])
            material_lines += _wrap_px(registry_note, mat_width, mat_font)
        else:
            material_lines = (_wrap(names_text, mat_width) if names_text else [])
            material_lines += _wrap(registry_note, mat_width)
    elif materials_in_appendix:
        material_lines = [APPENDIX_NOTE]

    attachment_texts = act['attachments']
    if materials_in_registry and act['cert_attachments']:
        attachment_texts = act['attachments'][act['cert_attachments']:]
    elif materials_in_appendix and act['cert_attachments']:
        attachment_texts = ([APPENDIX_ATTACHMENT_NOTE]
                            + act['attachments'][act['cert_attachments']:])

    att_font = _measure_font(ws.cell(row=ATTACHMENTS_FIRST_ROW, column=3))
    att_width = _slot_width_px(ws, ATTACHMENTS_FIRST_ROW, 3, 20)
    att_limit = _line_capacity(ws, ATTACHMENTS_FIRST_ROW, 3, 20)
    attachment_lines = []   # (номер или None для продолжения, текст)
    for n, text in enumerate(attachment_texts, start=1):
        parts = _wrap_px(text, att_width, att_font) if att_font else _wrap(text, att_limit)
        for k, line in enumerate(parts):
            attachment_lines.append((n if k == 0 else None, line))

    # --- Поля бланка: значение перетекает на следующие подчёркнутые строки,
    #     а если их не хватает — вставляются новые с оформлением последнего слота ---
    sro_contractor = ''
    if header['contractor_sro_name']:
        sro_contractor = (f"{header['contractor_sro_name']}, {header['contractor_sro_code']} "
                          f"от {_format_date(header['contractor_sro_date'])}")
    sro_designer = ''
    if header['designer_sro_name']:
        sro_designer = (f"{header['designer_sro_name']}, {header['designer_sro_code']} "
                        f"от {_format_date(header['designer_sro_date'])}")

    schemes = [a for a in attachment_texts if 'схем' in a.lower()]

    # (слоты — (строка, колонка от, колонка до) по пустому шаблону; текст или готовые строки;
    #  для приложений — номера пунктов)
    flows = [
        ([(2, 10, 20)], header['object_name'], None, None),
        ([(3, 2, 20)], f"по адресу: {header['object_address']}" if header['object_address'] else '', None, None),
        ([(6, 2, 20)], _org_line(header['customer_name'], header['customer_ogrn'], header['customer_inn']), None, None),
        ([(8, 2, 20), (10, 2, 20)], header['customer_address'], None, None),
        ([(13, 2, 20)], _org_line(header['contractor_name'], header['contractor_ogrn'], header['contractor_inn']), None, None),
        ([(15, 2, 20)], header['contractor_address'], None, None),
        ([(17, 2, 20)], sro_contractor, None, None),
        ([(20, 2, 20)], _org_line(header['designer_name'], header['designer_ogrn'], header['designer_inn']), None, None),
        ([(22, 2, 20)], header['designer_address'], None, None),
        ([(24, 2, 20)], sro_designer, None, None),
    ]
    for role, (row1, row2) in SIGNER_TOP_ROWS.items():
        signer = act['signers'].get(role)
        if not signer:
            continue
        line1 = signer_line1(role, signer)
        flows.append(([(row1, 2, 20)], line1, None, None))
        flows.append(([(row2, 2, 20)], signer_line2(signer), None, None))
    flows += [
        ([(67, 10, 20)], header['contractor_name'], None, None),
        ([(71, 2, 20), (72, 2, 20), (73, 2, 20)], None, work_lines, None),
        ([(76, 12, 20)], header['designer_name'], None, None),
        ([(77, 2, 20)], header['design_doc_reference'], None, None),
        ([(81, 2, 20), (82, 2, 20), (83, 2, 20)], None, material_lines, None),
        ([(86, 6, 20)], '; '.join(schemes), None, None),
        ([(95, 2, 20)], act['standards_text'], None, None),
        ([(98, 2, 20)], act['next_work_text'], None, None),
        ([(104, 3, 20), (105, 3, 20), (106, 3, 20)], None,
         [line for _, line in attachment_lines], [n for n, _ in attachment_lines]),
    ]
    for role, row in SIGNER_BOTTOM_ROWS.items():
        signer = act['signers'].get(role)
        if signer:
            flows.append(([(row, 12, 14)], signer['full_name'], None, None))

    # проход 1: раскладка по строкам — ширина слотов и текста считается по пустому шаблону
    laid_out = []
    for slots, text, ready_lines, numbers in flows:
        font = _measure_font(ws.cell(row=slots[0][0], column=slots[0][1]))
        if font is not None:
            widths = [_slot_width_px(ws, r, c1, c2) for r, c1, c2 in slots]
            lines = ready_lines if ready_lines is not None else _flow_lines_px(text, widths, font)
        else:
            caps = [_line_capacity(ws, r, c1, c2) for r, c1, c2 in slots]
            lines = ready_lines if ready_lines is not None else _flow_lines(text, caps)
        laid_out.append((slots, lines, numbers))
    laid_out.sort(key=lambda f: f[0][0][0])

    # проход 2: вставляем недостающие строки сверху вниз и собираем записи
    shift = 0
    shift_events = []       # (после какой строки шаблона вставлено, сколько)
    writes = []             # (строка, колонка, текст) в координатах готового файла
    for slots, lines, numbers in laid_out:
        extra = max(0, len(lines) - len(slots))
        last_r, last_c1 = slots[-1][0], slots[-1][1]
        if extra:
            _insert_rows_like(ws, at=last_r + 1 + shift, amount=extra, source_row=last_r + shift)
            shift_events.append((last_r, extra))
        for i, line in enumerate(lines):
            if i < len(slots):
                row, col = slots[i][0] + shift, slots[i][1]
            else:
                row, col = last_r + shift + (i - len(slots) + 1), last_c1
            if line:
                writes.append((row, col, line))
            if numbers and numbers[i]:
                writes.append((row, 2, f' {numbers[i]}.'))
        shift += extra

    def at(row):
        """Номер строки шаблона -> строка в готовом файле (с учётом вставок выше)."""
        return row + sum(amount for after, amount in shift_events if after < row)

    # --- Номер и дата акта ---
    writes.append((at(30), 3, act['act_number']))
    day, month, year = _format_date_parts(act['drawn_up_date'])
    writes.append((at(30), 14, day))     # N30
    writes.append((at(30), 16, month))   # P30
    writes.append((at(30), 19, year))    # S30

    # --- 5. Даты работ ---
    start_day, start_month, start_year = _format_date_parts(act['work_start_date'])
    end_day, end_month, end_year = _format_date_parts(act['work_end_date'])
    writes.append((at(91), 8, start_day))     # H91
    writes.append((at(91), 10, start_month))  # J91
    writes.append((at(91), 13, start_year))   # M91
    writes.append((at(92), 8, end_day))       # H92
    writes.append((at(92), 10, end_month))    # J92
    writes.append((at(92), 13, end_year))     # M92

    writes.append((at(100), 8, '-'))          # H100 — дополнительные сведения
    # «Акт составлен в N экземплярах» — вручную из карточки акта, по умолчанию 4
    writes.append((at(102), 6, act['copies_count'] or 4))       # F102

    for row, col, value in writes:
        ws.cell(row=row, column=col, value=value)

    _fit_merged_rows(ws)
    _apply_page_break(ws)
    _trim_sheet(ws)

    return wb


def signer_line1(role, signer):
    """Должность, организация, ФИО и приказ подписанта (приказ — у всех ролей, без задвоения)."""
    line1 = f"{signer['position']} {signer['org_short_name']}     {signer['full_name']}"
    if signer['order_document']:
        doc = signer['order_document'].strip()
        line1 += f", {doc}" if doc.lower().startswith('приказ') else f", приказ {doc}"
    return line1


def signer_line2(signer):
    """Вторая строка подписанта: НРС, если он есть, иначе реквизиты организации."""
    if signer.get('nrs_id'):
        return f"идентификационный номер {signer['nrs_id']}"
    org_parts = []
    if signer['org_ogrn']:
        org_parts.append(f"ОГРН {signer['org_ogrn']}")
    if signer['org_inn']:
        org_parts.append(f"ИНН {signer['org_inn']}")
    if signer['org_address']:
        org_parts.append(signer['org_address'])
    return ', '.join(org_parts)


def _page_starts_with_hint(pdf):
    """Вторая страница начинается с пояснения «(…)», а не с заголовка раздела?"""
    if len(pdf) < 2:
        return False
    h = pdf[1].get_size()[1]
    top = pdf[1].get_textpage().get_text_bounded(0, h - 140, 10000, h).strip()
    return top.startswith('(')


def _optimize_pagination(output_path):
    """Подбор разрыва реальной печатью: если вторая страница начинается с пояснения
    (обрыв раздела) или документ длиннее 2 страниц — перебираем границы разделов
    и оставляем первую, дающую не больше 2 страниц без обрыва.
    Если ничего не подходит — остаётся естественная разбивка Excel."""
    from .office import convert_xlsx_to_pdf as _xlsx_to_pdf
    from openpyxl.worksheet.pagebreak import Break, RowBreak

    base_pdf = output_path + '.base.pdf'
    try:
        import pypdfium2 as pdfium
        _xlsx_to_pdf(output_path, base_pdf)
        pdf = pdfium.PdfDocument(base_pdf)
        if len(pdf) <= 2 and not _page_starts_with_hint(pdf):
            pdf.close()
            os.remove(base_pdf)
            return
        pdf.close()
    except Exception:
        if os.path.exists(base_pdf):
            os.remove(base_pdf)
        return   # нет LibreOffice/конвертации — оставляем как есть

    wb = openpyxl.load_workbook(output_path)
    ws = wb[SHEET_NAME]
    candidates = []
    for row in ws.iter_rows(min_col=2, max_col=2):
        text = str(row[0].value or '')
        if text.startswith('произвели осмотр работ'):
            candidates.append(row[0].row - 3)
        elif text.startswith((' 1.', ' 2.', ' 3.')):
            candidates.append(row[0].row - 1)
    wb.close()

    for row_id in sorted({r for r in candidates if r > 0}):
        wb = openpyxl.load_workbook(output_path)
        ws = wb[SHEET_NAME]
        ws.row_breaks = RowBreak()          # сбрасываем прошлого кандидата
        ws.row_breaks.append(Break(id=row_id))
        wb.save(output_path)
        wb.close()
        try:
            _xlsx_to_pdf(output_path, base_pdf)
            pdf = pdfium.PdfDocument(base_pdf)
            if len(pdf) <= 2 and not _page_starts_with_hint(pdf):
                pdf.close()
                os.remove(base_pdf)
                return
            pdf.close()
        except Exception:
            break

    # никто не подошёл — убираем ручные разрывы, остаётся естественная разбивка
    wb = openpyxl.load_workbook(output_path)
    wb[SHEET_NAME].row_breaks = RowBreak()
    wb.save(output_path)
    wb.close()
    if os.path.exists(base_pdf):
        os.remove(base_pdf)


def generate_act_excel(act_id):
    """Сохраняет акт в Excel в любом состоянии: черновик с незаполненными полями — тоже.
    Чего не хватает, видно на странице акта (предупреждения) и в самом файле (строки «⚠ …»)."""
    act = build_act_context(act_id)
    act_obj = HiddenWorkAct.objects.select_related('section').get(id=act_id)
    header = header_for_act(act_obj)
    wb = render_act_workbook(act, header)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe_number = re.sub(r'[\\/:*?"<>|]+', '-', act['act_number'])   # номер может содержать косую черту
    filename = f"АОСР_№{safe_number}.xlsx"
    output_path = os.path.join(OUTPUT_DIR, filename)
    wb.save(output_path)

    return output_path
