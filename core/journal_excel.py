"""
core/journal_excel.py

Импорт журнала входного контроля из Excel (.xlsx).

Ожидаемый вид таблицы — как в журнале:
    № п/п | Дата поставки | Наименование материалов и конструкций | Ед. изм. |
    Кол-во | Поставщик | Наименование сопроводительного документа |
    Вид контроля | Результат контроля | Подпись лица, осуществляющего контроль

Строка заголовка ищется по названиям колонок, поэтому над таблицей может быть
шапка объекта и объединённые ячейки. Каждая строка становится позицией
накладной (DeliveryItem) и записью контроля (IncomingInspection).

Повторный импорт того же файла обновляет уже загруженные строки, а не дублирует их:
накладная ищется по объекту, дате и поставщику, позиция — по названию, количеству и единице.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction

from .models import (
    Counterparty, DeliveryItem, DeliveryNote, IncomingInspection, Material, MaterialAlias, Person,
)

# Колонки журнала: ключ → по каким словам в заголовке узнаём колонку.
HEADER_FIELDS = [
    ('date', ('дата поставки', 'дата')),
    ('material', ('наименование материал', 'материалы и конструкц')),
    ('unit', ('ед.изм', 'ед. изм', 'единица измерения')),
    ('qty', ('кол-во', 'количество')),
    ('supplier', ('поставщик',)),
    ('certificate', ('сопроводительн', 'документ о качестве', 'сертификат', 'декларац', 'паспорт качества')),
    ('control_type', ('вид контроля',)),
    ('result', ('результат контрол', 'результат')),
    ('inspector', ('подпись', 'проверил')),
]

REJECT_WORDS = ('не допущ', 'брак', 'отклон', 'дефект')


def _norm(value):
    text = str(value or '').lower().replace('ё', 'е').replace(' ', ' ')
    return re.sub(r'\s+', ' ', text).strip()


def _text(value):
    """Значение ячейки как текст. Числа без лишних нулей, даты — по-русски."""
    if value is None:
        return ''
    if isinstance(value, datetime):
        return value.strftime('%d.%m.%Y')
    if isinstance(value, date):
        return value.strftime('%d.%m.%Y')
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return str(int(value))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).replace(' ', ' ').strip()


def _as_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    for fmt in ('%d.%m.%Y', '%d.%m.%y', '%Y-%m-%d', '%d/%m/%Y', '%d.%m.%Y г.'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _as_decimal(value):
    if value is None or value == '':
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = _text(value).replace(' ', '').replace(',', '.')
    match = re.search(r'-?\d+(?:\.\d+)?', text)
    if not match:
        return None
    try:
        return Decimal(match.group())
    except InvalidOperation:
        return None


def _header_key(value):
    text = _norm(value)
    for key, words in HEADER_FIELDS:
        if any(word in text for word in words):
            return key
    return None


def _find_header(rows):
    """(индекс строки заголовка, {ключ: номер колонки})."""
    for index, row in enumerate(rows[:30]):
        mapping = {}
        for column, value in enumerate(row):
            key = _header_key(value)
            if key and key not in mapping:
                mapping[key] = column
        if 'date' in mapping and 'material' in mapping:
            return index, mapping
    raise ValueError(
        'Не найдена строка заголовка. Нужны колонки «Дата поставки» и '
        '«Наименование материалов и конструкций» — как в таблице журнала.'
    )


def _resolve_material(name, unit):
    """Материал из справочника: синоним, точное название или новый. (материал, статус, создан)."""
    alias = MaterialAlias.objects.filter(normalized=MaterialAlias.normalize(name)) \
        .select_related('material').first()
    if alias:
        return alias.material, DeliveryItem.Status.CONFIRMED, False
    material = Material.objects.filter(name__iexact=name).first()
    if material:
        return material, DeliveryItem.Status.CONFIRMED, False
    material = Material.objects.create(name=name[:200], unit=(unit or '')[:10])
    return material, DeliveryItem.Status.CONFIRMED, True


def _resolve_supplier(name):
    """Поставщик по точному названию; если такого нет — заводим контрагента."""
    supplier = Counterparty.objects.filter(short_name__iexact=name).first() \
        or Counterparty.objects.filter(full_name__iexact=name).first()
    if supplier:
        return supplier, False
    return Counterparty.objects.create(short_name=name[:200], inn=''), True


def _find_person(text):
    """Участник по ФИО из колонки «Подпись». Если не найден — None."""
    wanted = _norm(text)
    if not wanted:
        return None
    for person in Person.objects.only('last_name', 'first_name', 'middle_name'):
        full = _norm(f"{person.last_name} {person.first_name} {person.middle_name or ''}")
        if full == wanted:
            return person
    return None


def _rejected(result):
    text = _norm(result)
    return any(word in text for word in REJECT_WORDS)


def _note_for(site, day, supplier, has_quality_passport=False):
    """Накладная для группы «дата + поставщик». Повторный импорт её же и использует."""
    number = f"Импорт от {day:%d.%m.%Y}"
    note = DeliveryNote.objects.filter(site=site, delivery_date=day, supplier=supplier,
                                       number=number).first()
    if note:
        if has_quality_passport and not note.has_quality_passport:
            note.has_quality_passport = True
            note.save(update_fields=['has_quality_passport'])
        return note, False
    return DeliveryNote.objects.create(
        site=site, delivery_date=day, supplier=supplier, number=number,
        acceptance_status='', has_quality_passport=has_quality_passport), True


def import_xlsx(file_obj, site):
    """Импорт файла. Возвращает статистику и список ошибок по строкам."""
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(file_obj, data_only=True, read_only=True)
    except Exception as exc:
        raise ValueError(f'Не удалось прочитать файл Excel: {exc}') from exc

    try:
        sheet = workbook.active
        rows = [tuple(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()

    if not any(any(cell not in (None, '') for cell in row) for row in rows):
        raise ValueError('Файл пустой.')

    header_index, mapping = _find_header(rows)

    result = {'rows': 0, 'notes': 0, 'materials': 0, 'counterparties': 0, 'errors': []}

    def cell(row, key):
        column = mapping.get(key)
        return row[column] if column is not None and column < len(row) else None

    last_supplier = ''
    with transaction.atomic():
        for row_number, row in enumerate(rows[header_index + 1:], start=header_index + 2):
            name = _text(cell(row, 'material'))
            supplier_text = _text(cell(row, 'supplier')) or last_supplier
            quantity_value = cell(row, 'qty')
            day = _as_date(cell(row, 'date'))

            # пустая строка или итоговая строка без названия и поставщика
            if not name and not supplier_text:
                continue
            if not name:
                result['errors'].append(f'Строка {row_number}: не указано наименование материала.')
                continue
            if _norm(name).startswith(('итого', 'всего')):
                continue
            if day is None:
                result['errors'].append(f'Строка {row_number}: не распознана дата поставки.')
                continue
            if not supplier_text:
                result['errors'].append(f'Строка {row_number}: не указан поставщик.')
                continue
            last_supplier = supplier_text

            unit = _text(cell(row, 'unit'))[:20]
            quantity = _as_decimal(quantity_value)
            certificate_text = _text(cell(row, 'certificate'))
            control_type = _text(cell(row, 'control_type'))[:200]
            inspection_result = _text(cell(row, 'result'))[:200]
            inspector_text = _text(cell(row, 'inspector'))[:200]

            supplier, supplier_created = _resolve_supplier(supplier_text)
            if supplier_created:
                result['counterparties'] += 1

            note, note_created = _note_for(site, day, supplier, bool(certificate_text))
            if note_created:
                result['notes'] += 1

            material, matching_status, material_created = _resolve_material(name[:200], unit)
            if material_created:
                result['materials'] += 1

            item = DeliveryItem.objects.filter(
                delivery_note=note, original_text=name[:300], quantity=quantity, unit=unit).first()
            if item is None:
                item = DeliveryItem(delivery_note=note, original_text=name[:300],
                                    quantity=quantity, unit=unit)
            item.material = material
            item.matching_status = matching_status
            item.certificate_text = certificate_text
            item.save()
            result['rows'] += 1

            if not (control_type or inspection_result or inspector_text):
                continue

            inspector = _find_person(inspector_text)
            inspection = IncomingInspection.objects.filter(delivery_item=item) \
                .order_by('-inspection_date', '-pk').first()
            if inspection is None:
                inspection = IncomingInspection(delivery_item=item)
            inspection.inspection_date = day
            inspection.control_type = control_type
            inspection.inspection_result = inspection_result
            inspection.inspector = inspector
            inspection.inspector_name = '' if inspector else inspector_text
            inspection.usage_permission = not _rejected(inspection_result)
            inspection.save()

    return result
