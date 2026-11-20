r"""
core/cert_recognition.py

Распознавание документов о качестве: сертификаты, декларации, отказные письма.
Модуль не зависит от Django и работает локально — документы никуда не отправляются.

Что нужно установить:
    pip install pypdfium2 pytesseract pillow opencv-python-headless pyzbar
    + программа Tesseract с русским языком. Windows: установщик UB Mannheim
      (https://github.com/UB-Mannheim/tesseract/wiki), при установке отметить Russian.
      Если программа стоит не в C:\Program Files\Tesseract-OCR — путь в переменной окружения:
      TESSERACT_CMD=D:\...\tesseract.exe

Главная функция:
    result = recognize_file(path_or_bytes, preview_dir=None, on_progress=None)
    result['documents'] -> список найденных документов с полями
"""

import logging
import os
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO

logger = logging.getLogger(__name__)

OCR_DPI = 300          # разрешение для распознавания
PREVIEW_DPI = 110      # разрешение картинок для экрана проверки

MONTHS = {
    'янв': 1, 'фев': 2, 'мар': 3, 'апр': 4, 'мая': 5, 'май': 5, 'июн': 6,
    'июл': 7, 'авг': 8, 'сен': 9, 'окт': 10, 'ноя': 11, 'дек': 12,
}

DOC_CERTIFICATE = 'Сертификат соответствия'
DOC_DECLARATION = 'Декларация о соответствии'
DOC_REFUSAL = 'Отказное письмо'


# ===========================================================================
# 1. Tesseract: где он и есть ли русский язык
# ===========================================================================

def find_tesseract(explicit=None):
    """Путь к tesseract или None. Ищет в настройке, в PATH и в стандартных папках Windows."""
    candidates = [explicit, os.environ.get('TESSERACT_CMD'), shutil.which('tesseract'),
                  r'C:\Program Files\Tesseract-OCR\tesseract.exe',
                  r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe']
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def ocr_status(tesseract_cmd=None):
    """(готово ли распознавание, текст для человека)."""
    try:
        import pytesseract
    except ImportError:
        return False, 'Не установлена библиотека pytesseract (pip install pytesseract).'
    cmd = find_tesseract(tesseract_cmd)
    if not cmd:
        return False, 'Не найдена программа Tesseract. Установите её — см. ИНСТРУКЦИЯ.md.'
    pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        langs = pytesseract.get_languages(config='')
    except Exception:
        logger.exception('Tesseract не запускается')
        return False, 'Tesseract не запускается. Проверьте установку и путь TESSERACT_CMD.'
    if 'rus' not in langs:
        return False, 'В Tesseract нет русского языка (rus). Переустановите его с отметкой «Russian».'
    return True, 'Распознавание готово к работе.'


# ===========================================================================
# 2. Страницы файла: текстовый слой, картинка, скан это или нет
# ===========================================================================

def _pages_from_file(data, name=''):
    """Список страниц: {'image': PIL (OCR_DPI), 'text_layer': str, 'scanned': bool}."""
    from PIL import Image

    if name.lower().endswith(('.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp')) or data[:4] in (b'\x89PNG', b'\xff\xd8\xff\xe0', b'\xff\xd8\xff\xe1'):
        img = Image.open(BytesIO(data))
        img.load()
        return [{'image': img.convert('RGB'), 'text_layer': '', 'scanned': True}]

    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(data)
    pages = []
    for page in pdf:
        width, height = page.get_size()
        text_layer = page.get_textpage().get_text_range() or ''

        # скан = на странице есть картинка почти во весь лист
        scanned, scan_dpi = False, None
        for obj in page.get_objects():
            if obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE:
                get_box = getattr(obj, 'get_bounds', None) or getattr(obj, 'get_pos')   # имя зависит от версии
                left, bottom, right, top = get_box()
                if (right - left) * (top - bottom) > 0.6 * width * height:
                    scanned = True
                    try:
                        px_w, px_h = obj.get_px_size()
                        scan_dpi = max(px_w, px_h) / (max(right - left, top - bottom) / 72)
                    except Exception:
                        scan_dpi = None
                    break

        # Скан рисуем в его родном разрешении (слои PDF складываются правильно, мелкий текст не портится),
        # потом увеличиваем до OCR_DPI сглаживающим фильтром — так Tesseract читает лучше всего.
        if scanned and scan_dpi and scan_dpi < OCR_DPI - 20:
            native_dpi = max(100, min(scan_dpi, OCR_DPI))
            image = page.render(scale=native_dpi / 72).to_pil().convert('RGB')
            k = OCR_DPI / native_dpi
            image = image.resize((int(image.width * k), int(image.height * k)), Image.LANCZOS)
        else:
            image = page.render(scale=OCR_DPI / 72).to_pil().convert('RGB')
        pages.append({'image': image, 'text_layer': text_layer, 'scanned': scanned})
    return pages


# ===========================================================================
# 3. OCR страницы + отдельное распознавание строки с номером
# ===========================================================================

NUMBER_LINE = re.compile(r'(Р|P)\s*(О|O|0)\s*(С|C)\s*(С|C)\b|Е\s*А\s*Э\s*С|EAЭC|ЕАЭC')


def _ocr_page(image):
    """Текст страницы + варианты строки с номером (вырезанной и распознанной отдельно)."""
    import pytesseract
    from PIL import Image

    gray = image.convert('L')
    data = pytesseract.image_to_data(gray, lang='rus+eng', output_type=pytesseract.Output.DICT)

    lines = {}
    for i, word in enumerate(data['text']):
        if not word.strip():
            continue
        key = (data['block_num'][i], data['par_num'][i], data['line_num'][i])
        lines.setdefault(key, []).append(i)

    text_lines, number_lines = [], []
    for key in sorted(lines, key=lambda k: (min(data['top'][i] for i in lines[k]), k)):
        idx = lines[key]
        line_text = ' '.join(data['text'][i] for i in idx)
        text_lines.append(line_text)
        top = min(data['top'][i] for i in idx)
        if NUMBER_LINE.search(line_text) and len(re.findall(r'\d', line_text)) >= 4 \
                and top < gray.height * 0.45:
            box = (min(data['left'][i] for i in idx), top,
                   max(data['left'][i] + data['width'][i] for i in idx),
                   max(data['top'][i] + data['height'][i] for i in idx))
            number_lines.append((line_text, box))

    variants = []
    for line_text, (x0, y0, x1, y1) in number_lines[:3]:
        crop = gray.crop((max(0, x0 - 20), max(0, y0 - 12), x1 + 20, y1 + 12))
        reads = []
        # строку с номером читаем несколько раз в разном масштабе — выберем ту, что подходит под формат номера
        for k in (1, 1.5, 2):
            c = crop.resize((int(crop.width * k), int(crop.height * k)), Image.LANCZOS) if k != 1 else crop
            for lang in ('rus', 'rus+eng'):
                reads.append(pytesseract.image_to_string(c, lang=lang, config='--psm 7').strip())
        variants.append({'line': line_text, 'crop': reads[0], 'reads': reads})

    return '\n'.join(text_lines), variants


def _read_qr(image):
    """Все QR-коды страницы (обычно это ссылка на реестр или на проверку подлинности)."""
    found = []
    try:
        from pyzbar.pyzbar import decode, ZBarSymbol
        for d in decode(image, symbols=[ZBarSymbol.QRCODE]):
            found.append(d.data.decode('utf-8', 'replace'))
    except Exception:
        pass
    if not found:
        try:
            import cv2
            import numpy as np
            arr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
            det = cv2.QRCodeDetector()
            h, w = arr.shape[:2]
            regions = [arr] + [arr[y:y + h // 2, x:x + w // 2] for y in (0, h // 2) for x in (0, w // 2)]
            for region in regions:
                value, *_ = det.detectAndDecode(region)
                if value:
                    found.append(value)
                    break
        except Exception:
            pass
    return list(dict.fromkeys(found))


# ===========================================================================
# 4. Номер документа: правила исправления типичных ошибок распознавания
# ===========================================================================

LAT_TO_CYR = str.maketrans('ABCEHKMOPTXYaceopxy', 'АВСЕНКМОРТХУасеорху')
DIGIT_LIKE = {'О': '0', 'O': '0', 'о': '0', 'o': '0', 'З': '3', 'з': '3', '!': '1', '|': '1',
              'l': '1', 'I': '1', 'i': '1', 'б': '6', 'S': '5', 'Б': '6'}

NUM_NEW = re.compile(r'^(РОСС RU|ЕАЭС N RU|ЕАЭС RU) ([СД])-([A-Z]{2})\.([А-Я]{2}\d{2})\.([А-Я])\.(\d{5})/(\d{2})$')
NUM_OLD = re.compile(r'^РОСС RU\.[А-Я]{2}\d{2}\.[А-Я]\d{5}$')
NUM_VOLUNTARY = re.compile(r'^РОСС RU\.\d{4,5}\.[0-9А-Я]{2,8}\.[0-9А-Я]{2,8}\.\d{3,6}$')


def _fix_digits_near_digits(segment):
    """Буква, похожая на цифру и стоящая рядом с цифрой, — это цифра (ОСПЗ2 -> ОСП32, АЩО1 -> АЩ01)."""
    chars = list(segment)
    for _ in range(2):
        for i, ch in enumerate(chars):
            if ch in DIGIT_LIKE:
                left = chars[i - 1] if i > 0 else ''
                right = chars[i + 1] if i + 1 < len(chars) else ''
                if left.isdigit() or right.isdigit():
                    chars[i] = DIGIT_LIKE[ch]
    return ''.join(chars)


def normalize_number(raw):
    """Приводит распознанный номер к правильному виду. Возвращает строку (может остаться кривой)."""
    s = raw.replace('№', ' ').replace('Ne ', ' ').replace('No ', ' ')
    s = re.sub(r'^.*?(?=(Р|P)\s*(О|O|0)\s*(С|C)\s*(С|C)|Е\s*А\s*Э\s*С|EAЭC|ЕАЭC)', '', s, flags=re.S)
    s = re.sub(r'(Р|P)\s*(О|O|0)\s*(С|C)\s*(С|C)', 'РОСС', s, count=1)
    s = re.sub(r'(Е|E)\s*(А|A)\s*Э\s*(С|C)', 'ЕАЭС', s, count=1)
    s = s.split(';')[0].split(',')[0].strip()
    s = re.split(r'\s{2,}|\s(?=[а-яё]{3,})', s)[0]          # хвост из обычных слов отрезаем
    s = re.sub(r'\s*\.\s*', '.', s)
    s = re.sub(r'\s*/\s*', '/', s)
    s = re.sub(r'\s*-\s*', '-', s)

    # код страны после РОСС / ЕАЭС N: распознаётся как КО, ВЦ, В0, KU, BU...
    s = re.sub(r'^(РОСС|ЕАЭС(?: N)?)\s*[A-Za-zА-Яа-я0-9]{2}(?=[.\s])', lambda m: m.group(1) + ' RU', s)
    s = re.sub(r'^ЕАЭС\s+(?:N|И|Н)\s+', 'ЕАЭС N ', s)

    m = re.match(r'^(РОСС RU|ЕАЭС N RU|ЕАЭС RU)\s*([СДCД])\s*-\s*(..)\.(.{3,6})\.(.)\.(.{5})/(.{2})', s)
    if m:                                                    # новый формат: С-RU.АЩ01.В.00760/25
        prefix, kind, country, org, scheme, serial, year = m.groups()
        kind = kind.translate(LAT_TO_CYR)
        country = 'RU' if country.upper() in ('RU', 'ВИ', 'ВЦ', 'КО', 'KU', 'BU', 'RИ', 'РU', 'КU') else country.upper()
        letters, digits = org[:-2], org[-2:]
        if len(letters) > 2 and letters[2:].upper() in ('О', 'O', 'Q'):
            letters = letters[:2]
        for wrong, right in (('LJ', 'Щ'), ('LЦ', 'Щ'), ('Lц', 'Щ'), ('IO', 'Ю'), ('bI', 'Ы'), ('III', 'Ш'), ('W', 'Ш')):
            letters = letters.replace(wrong, right)
        org = letters.upper().translate(LAT_TO_CYR) + ''.join(DIGIT_LIKE.get(c, c) for c in digits)
        scheme = scheme.upper().translate(LAT_TO_CYR)
        serial = ''.join(DIGIT_LIKE.get(c, c) for c in serial)
        year = ''.join(DIGIT_LIKE.get(c, c) for c in year)
        return f'{prefix} {kind}-{country}.{org}.{scheme}.{serial}/{year}'

    if s.startswith('РОСС RU.'):                             # старый ГОСТ Р и добровольные системы
        head, *rest = s.split('.')
        rest = [_fix_digits_near_digits(seg.upper().translate(LAT_TO_CYR)) for seg in rest]
        return '.'.join([head] + rest)
    return s


def number_quality(number):
    """3 — номер точно в правильном формате, 1 — похож, 0 — мусор."""
    if NUM_NEW.match(number) or NUM_OLD.match(number) or NUM_VOLUNTARY.match(number):
        return 3
    if re.match(r'^(РОСС|ЕАЭС)', number) and len(re.findall(r'\d', number)) >= 5:
        return 1
    return 0


def pick_number(candidates):
    """Из нескольких прочтений выбирает лучшее. -> (номер, уверенность: high/low/none)."""
    scored = []
    for raw in candidates:
        n = normalize_number(raw)
        if n:
            scored.append((number_quality(n), n))
    if not scored:
        return '', 'none'
    scored.sort(key=lambda x: -x[0])
    best_q, best = scored[0]
    if best_q == 3:
        return best, 'high'
    return best, 'low' if best_q else 'none'


# ===========================================================================
# 5. Разбор полей документа
# ===========================================================================

def _parse_date(s):
    s = s.strip().translate(str.maketrans({'О': '0', 'o': '0', 'O': '0', 'З': '3', ',': '.'}))
    m = re.match(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', s)
    if m:
        d, mth, y = map(int, m.groups())
        try:
            return date(y, mth, d)
        except ValueError:
            return None
    m = re.match(r'(\d{1,2})\s+([А-Яа-я]{3})[а-я]*\.?\s+(\d{4})', s)
    if m and m.group(2).lower()[:3] in MONTHS:
        try:
            return date(int(m.group(3)), MONTHS[m.group(2).lower()[:3]], int(m.group(1)))
        except ValueError:
            return None
    return None


DATE_RE = r'(\d{1,2}[.,]\d{1,2}[.,]\d{4}|\d{1,2}\s+[А-Яа-я]{3,9}\s+\d{4})'


def _flat(text):
    text = re.sub(r"(?m)^[\s|'‘’‚°`^~•.,:;=—–-]{1,4}(?=\S)", '', text)      # мусор в начале строк
    text = re.sub(r"\s[|'‘’‚°`^~•]+(?=\s)", ' ', text)                     # одиночные знаки между словами
    return re.sub(r'\s+', ' ', text)


def _block(text, starts, stops, limit=900):
    """Кусок текста от заголовка до следующего заголовка."""
    flat = _flat(text)
    for start in starts:
        m = re.search(start, flat)
        if m:
            tail = flat[m.end():m.end() + limit]
            ends = [re.search(stop, tail) for stop in stops]
            end = min([e.start() for e in ends if e] or [len(tail)])
            return tail[:end].strip(' :;,.-—')
    return ''


HEADINGS = [r'ОРГАН\s+\S{1,3}\s+СЕРТИФИКАЦИИ', r'СЕРТИФИКАТ ВЫДАН', r'НА ОСНОВАНИИ', r'ПРОДУК[ЦНИ]{1,2}Я', r'ЗАЯВИТЕЛЬ',
            r'СООТВЕТСТВУЕТ', r'ИЗГОТОВИТЕЛЬ', r'Адрес \(адреса\)', r'и представленных', r'ДОПОЛНИТЕЛЬНАЯ',
            r'СЕРТИФИКАТ СООТВЕТСТВИЯ ВЫДАН', r'код ОК', r'Код ОКПД', r'код ТН', r'полное наименование',
            r'Место нахождения', r'Декларация о соответствии принята', r'ОРГАНОМ ПО СЕРТИФИКАЦИИ ПРИНЯТО']

LEGAL_FORMS = [
    ('Общество с ограниченной ответственностью', 'ООО'), ('Публичное акционерное общество', 'ПАО'),
    ('Закрытое акционерное общество', 'ЗАО'), ('Открытое акционерное общество', 'ОАО'),
    ('Непубличное акционерное общество', 'АО'), ('Акционерное общество', 'АО'),
    ('Индивидуальный предприниматель', 'ИП'),
]


def _org_from_block(block):
    """Организация из куска текста: краткое имя, полное, ИНН, ОГРН, адрес."""
    if not block:
        return {}
    inn = re.search(r'ИНН[:\s№/]*(\d{10}|\d{12})\b', block)
    ogrn = re.search(r'ОГРН[:\s№]*(\d{13}|\d{15})\b', block)
    address = re.search(r'(?<![\d])(\d{6},\s*(?!ОГРН|ИНН)[^;]{10,160}?)(?=[,;.]?\s*(?:Телефон|тел\.|тел:|e-mail|электронная|ОГРН|ИНН|$))', block)

    full = re.split(r',|\s+ИНН|\s+ОГРН|\s+место нахождения', block)[0].strip(' «»"')
    short = ''
    m = re.search(r'\(((?:ООО|АО|ПАО|ЗАО|ОАО|ИП)\s*[«"“][^)]{2,80})\)', block)
    if m:
        short = m.group(1).strip()
    else:
        short = full
        for long_form, abbr in LEGAL_FORMS:
            if short.lower().startswith(long_form.lower()):
                short = abbr + short[len(long_form):]
                break
    short = re.sub(r'\s+', ' ', short).strip()
    if short.count('«') > short.count('»'):
        short += '»' * (short.count('«') - short.count('»'))
    if full.count('«') > full.count('»'):
        full += '»' * (full.count('«') - full.count('»'))
    return {
        'short_name': short[:200],
        'full_name': full[:200],
        'inn': inn.group(1) if inn else '',
        'ogrn': ogrn.group(1) if ogrn else '',
        'address': address.group(1).strip()[:300] if address else '',
    }


def _standards(text):
    found = re.findall(r'(ГОСТ\s*(?:Р\s*)?(?:ISO\s*|EN\s*)?\d[\d.]*[-–]\d{2,4}|ТУ\s*\d[\d.\-–]{6,}\d|(?<![А-ЯA-Z])СП\s*\d+\.\d+[\d.]*)', _flat(text))
    clean = []
    for s in found:
        s = re.sub(r'\s+', ' ', s).replace('–', '-')
        if s not in clean:
            clean.append(s)
    return clean[:15]


def _classify(text):
    flat = _flat(text)
    up = flat.upper()
    if re.search(r'Отказать в проведении|не\s+подлежит|не\s+включена\s+в|не требуется|'
                 r'не требует(ся)? (обязательн|сертиф|подтвержд)|отказное письмо', flat, re.I):
        return DOC_REFUSAL
    if 'ДЕКЛАРАЦИЯ О СООТВЕТСТВИИ' in up or re.search(r'\bД-[A-ZА-Я]{2}\.', flat):
        return DOC_DECLARATION
    if 'СЕРТИФИКАТ СООТВЕТСТВИЯ' in up or 'СЕРТИФИКАТ COОТВЕТСТВИЯ' in up:
        return DOC_CERTIFICATE
    return ''


def _parse_document(text, number_variants, qr_values):
    flat = _flat(text)
    kind = _classify(text)
    voluntary = bool(re.search(r'добровольн', flat, re.I))
    doc = {
        'document_type': kind,
        'voluntary': voluntary,
        'number': '', 'number_confidence': 'none',
        'issue_date': None, 'expiry_date': None,
        'manufacturer': {}, 'issuer': '', 'product': '',
        'standards': _standards(text),
        'registry_url': '', 'check_url': '',
        'warnings': [],
    }

    for v in qr_values:
        if 'fsa.gov.ru' in v:
            doc['registry_url'] = v
        elif v.startswith('http') and not doc['check_url']:
            doc['check_url'] = v

    # --- номер и даты ---
    if kind in (DOC_CERTIFICATE, DOC_DECLARATION):
        candidates = []
        for v in number_variants:
            candidates += [v['line']] + v.get('reads', [v.get('crop', '')])
        m = re.search(r'(?:Регистрационный номер|№)\s*((?:РОСС|ЕАЭС)[^\n]{8,60})', text)
        if m:
            candidates.append(m.group(1))
        doc['number'], doc['number_confidence'] = pick_number(candidates)

        m = re.search(r'срок действия[^.]{0,60}?\b[сc]\s*' + DATE_RE + r'\s*(?:г\.?)?\s*по\s*' + DATE_RE, flat, re.I)
        if m:
            doc['issue_date'], doc['expiry_date'] = _parse_date(m.group(1)), _parse_date(m.group(2))
        if not doc['issue_date']:
            m = re.search(r'Дата регистрации[^:]{0,60}:?\s*' + DATE_RE, flat)
            if m:
                doc['issue_date'] = _parse_date(m.group(1))
        if not doc['expiry_date']:
            m = re.search(r'действительн\w*[^.]{0,60}?по\s*' + DATE_RE, flat)
            if m:
                doc['expiry_date'] = _parse_date(m.group(1))

    elif kind == DOC_REFUSAL:
        m = re.search(r'РЕШЕНИЕ.{0,30}?№[\s—–-]*(\d[\w/\-]{0,30}).{0,25}?' + DATE_RE, flat)
        if m:
            doc['number'], doc['issue_date'] = m.group(1), _parse_date(m.group(2))
            doc['number_confidence'] = 'high' if doc['issue_date'] else 'low'
        else:
            m = re.search(r'№\s*([0-9][\w/\-]{1,30})', flat)
            if m:
                doc['number'], doc['number_confidence'] = m.group(1), 'low'
            for m in re.finditer(DATE_RE, flat[:500]):          # дата письма — только в шапке
                if re.search(r'вх\.?[^.]{0,25}$|На\s*№[^.]{0,30}$', flat[max(0, m.start() - 40):m.start()]):
                    continue                      # это дата входящего запроса, а не письма
                doc['issue_date'] = _parse_date(m.group(1))
                break
            doc['warnings'].append('Номер и дату письма проверьте по документу — они часто вписаны от руки.')

    # --- изготовитель ---
    STRONG = [r'ОРГАН\s+\S{1,3}\s+СЕРТИФИКАЦИИ', r'СЕРТИФИКАТ ВЫДАН', r'НА ОСНОВАНИИ', r'ПРОДУК[ЦНИ]{1,2}Я', r'ЗАЯВИТЕЛЬ',
              r'ИЗГОТОВИТЕЛЬ', r'Изготовитель:', r'[НнH][аa] проведение сертификации продукции\s*:', r'ПРИНЯТО',
              r'Номер телефона']

    LEGAL = (r'(?:ОБЩЕСТВО|Общество|АКЦИОНЕРНОЕ|Акционерное|ПУБЛИЧНОЕ|Публичное|ЗАКРЫТОЕ|Закрытое|ОТКРЫТОЕ|'
             r'Открытое|Индивидуальный|ИНДИВИДУАЛЬНЫЙ|ООО|АО|ПАО|ЗАО|ОАО|ИП)\b')

    def org_at(starts):
        for start in starts:
            m = re.search(start, flat)
            if not m:
                continue
            after = flat[m.end():m.end() + 1200]
            stop = min([x.start() for x in (re.search(st, after) for st in STRONG) if x] or [len(after)])
            wide = after[:stop]
            if re.match(r'\s*[—–:-]?\s*' + LEGAL, after):
                name_src = _block(flat[m.start():], [start], HEADINGS)
            else:
                # в бланках «значение, а под ним подпись поля» название стоит перед подписью
                before = flat[max(0, m.start() - 260):m.start()]
                found = list(re.finditer(LEGAL, before))
                name_src = before[found[-1].start():] if found else _block(flat[m.start():], [start], HEADINGS)
                wide = before[found[-1].start():] + ' ' + wide if found else wide
            org = _org_from_block(name_src)
            extra = _org_from_block(wide)
            for key in ('inn', 'ogrn', 'address'):
                org[key] = org.get(key) or extra.get(key, '')
            return org
        return {}

    org = org_at([r'ИЗГОТОВИТЕЛЬ', r'Изготовитель\s*:'])
    if not org.get('inn'):
        alt_org = org_at([r'ЗАЯВИТЕЛЬ', r'заявителя\s*:', r'Заявитель\s*:'])
        if alt_org.get('inn') or not org.get('short_name'):
            org = {**org, **{k: v for k, v in alt_org.items() if v and not org.get(k)}} if org else alt_org
    if kind == DOC_REFUSAL and not org.get('short_name'):
        m = re.search(r'((?:ООО|АО|ПАО|ЗАО)\s*[«"“]\s*[^»"”]{2,60}[»"”])', flat)
        if m:
            org = {'short_name': re.sub(r'\s+', ' ', m.group(1)).replace('« ', '«').replace(' »', '»'),
                   'full_name': '', 'inn': '', 'ogrn': '', 'address': ''}
    doc['manufacturer'] = org

    # --- кто выдал ---
    issuer_block = _block(text, [r'ОРГАН\s+\S{1,3}\s+СЕРТИФИКАЦИИ'], HEADINGS[1:], limit=300)
    if issuer_block:
        body = re.sub(r'^№?\s*(?:РОСС\s*)?[\w.]*\d[\w.]*\s*,?\s*', '', issuer_block)
        org_issuer = _org_from_block(body)
        name = org_issuer.get('short_name', '')
        if not name:   # «Орган по сертификации … «Название»» без формы собственности
            name = _flat(body)
        name = re.split(r'[.,]?\s*(?:ОГРН|ИНН|[Мм]есто нахождения|[Аа]дрес|тел\.|Аттестат|рег\.)', name, maxsplit=1)[0]
        doc['issuer'] = name.strip(" ,.‚")[:200]
    else:
        first = next((l for l in text.splitlines() if len(l.strip()) > 20), '')
        doc['issuer'] = re.sub(r'^[а-яa-z]\s+', '', _flat(first).strip(" '‘’‚+.,$*©"))[:200]

    # --- продукция ---
    product = _block(text, [r'ПОДТВЕРЖДАЕТ,?\s+ЧТО\s+ПРОДУК\S*', r'ПРОДУК[ЦНИ]{1,2}Я', r'[НнH][аa] проведение сертификации продукции\s*:',
                            r'Продукция с\s'],
                     [r'СООТВЕТСТВУЕТ', r'Код ОКПД', r'ИЗГОТОВИТЕЛЬ', r'наименование и обозначение', r'не включена',
                      r'не подлежит', r'Изготовитель\s*:'], limit=1500)
    product = re.sub(r'код\s*(?:ОКПД\s*2?|ОК|[ТT][НH]\s*ВЭД(?:\s*ЕАЭ[СC]?)?)\s*:?\s*[\d. ]*', ' ', product, flags=re.I)
    doc['product'] = re.sub(r'\s+', ' ', product).strip(' .;,')[:1500]

    if kind in (DOC_CERTIFICATE, DOC_DECLARATION):
        if doc['number_confidence'] != 'high':
            doc['warnings'].append('Номер распознан неуверенно — сверьте с документом.')
        if not doc['expiry_date']:
            doc['warnings'].append('Не найден срок действия — укажите вручную.')
    if not kind:
        doc['warnings'].append('Не удалось определить тип документа (это сертификат, декларация или отказное письмо?).')
    if doc['issue_date'] and doc['expiry_date'] and doc['expiry_date'] < date.today():
        doc['warnings'].append(f"Срок действия истёк {doc['expiry_date']:%d.%m.%Y}.")
    return doc


# ===========================================================================
# 6. Пачка: деление на документы
# ===========================================================================

def _starts_new_document(page_text):
    head = _flat('\n'.join(page_text.splitlines()[:18])).upper()
    if re.search(r'\bПРИЛОЖЕНИЕ\b', head[:200]):
        return False
    return bool(re.search(r'СЕРТИФИКАТ СООТВЕТСТВИЯ|ДЕКЛАРАЦИЯ О СООТВЕТСТВИИ|\bРЕШЕНИЕ\b|'
                          r'ФЕДЕРАЛЬНАЯ СЛУЖБА|МИНИСТЕРСТВО|ПАСПОРТ КАЧЕСТВА|ДОКУМЕНТ О КАЧЕСТВЕ|\bПИСЬМО\b', head))


def recognize_file(data, name='', preview_dir=None, on_progress=None, tesseract_cmd=None):
    """
    Главная функция. data — содержимое файла (bytes) или путь.
    preview_dir — куда сохранить картинки страниц для экрана проверки (page_1.jpg ...).
    on_progress(text) — сообщения о ходе работы.
    """
    if isinstance(data, str):
        name = name or data
        with open(data, 'rb') as f:
            data = f.read()
    progress = on_progress or (lambda text: None)

    ok, message = ocr_status(tesseract_cmd)
    progress('Читаю файл…')
    pages = _pages_from_file(data, name)

    if preview_dir:
        os.makedirs(preview_dir, exist_ok=True)
        for n, p in enumerate(pages, start=1):
            img = p['image']
            ratio = PREVIEW_DPI / OCR_DPI
            img.resize((int(img.width * ratio), int(img.height * ratio))).convert('RGB').save(
                os.path.join(preview_dir, f'page_{n}.jpg'), quality=80)

    need_ocr = [i for i, p in enumerate(pages) if p['scanned'] or len(p['text_layer'].strip()) < 100]
    if need_ocr and not ok:
        for p in pages:
            p['text'], p['number_variants'] = p['text_layer'], []
        warnings = [message]
    else:
        warnings = []
        done = [0]

        def work(i):
            p = pages[i]
            if i in need_ocr:
                p['text'], p['number_variants'] = _ocr_page(p['image'])
            else:
                p['text'] = p['text_layer']
                p['number_variants'] = [{'line': l, 'crop': ''} for l in p['text_layer'].splitlines()
                                        if NUMBER_LINE.search(l) and len(re.findall(r'\d', l)) >= 4][:4]
            done[0] += 1
            progress(f'Распознаю страницы: {done[0]} из {len(pages)}')

        os.environ.setdefault('OMP_THREAD_LIMIT', '1')
        with ThreadPoolExecutor(max_workers=max(1, min(4, (os.cpu_count() or 2)))) as pool:
            list(pool.map(work, range(len(pages))))

    progress('Ищу QR-коды…')
    for p in pages:
        p['qr'] = _read_qr(p['image'])

    # деление пачки на документы
    groups = []
    for n, p in enumerate(pages, start=1):
        if not groups or _starts_new_document(p['text']):
            groups.append([])
        groups[-1].append((n, p))

    progress('Разбираю поля…')
    documents = []
    for group in groups:
        text = '\n'.join(p['text'] for _, p in group)
        variants = [v for _, p in group[:1] for v in p['number_variants']]
        qr = [v for _, p in group for v in p['qr']]
        doc = _parse_document(text, variants, qr)
        doc['pages'] = [n for n, _ in group]
        doc['text'] = text
        documents.append(doc)

    return {
        'pages_count': len(pages),
        'ocr_used': bool(need_ocr) and ok,
        'documents': documents,
        'warnings': warnings,
    }
