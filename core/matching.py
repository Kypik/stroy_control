"""
core/matching.py

Сопоставление строк накладной с материалами справочника.

Порядок:
  1. синоним — строка уже встречалась и её сопоставили вручную (MaterialAlias) → «подтверждено»;
  2. похожее название — совпадает большая часть слов → «автоматически опознано» (инженер проверяет);
  3. иначе — «не опознано», инженер выбирает сам, и выбор запоминается как синоним.
"""

import re
from decimal import Decimal, InvalidOperation

from .models import DeliveryItem, Material, MaterialAlias

_WORD = re.compile(r'[a-zа-яё]{3,}|\d+(?:[.,]\d+)?', re.I)
MIN_SIMILARITY = 0.6     # доля слов названия материала, которые нашлись в строке накладной


def _tokens(text):
    return {w.lower().replace(',', '.')[:6] for w in _WORD.findall(text or '')}


def find_material(text):
    """(материал или None, статус DeliveryItem.Status)."""
    alias = MaterialAlias.objects.filter(normalized=MaterialAlias.normalize(text)).select_related('material').first()
    if alias:
        return alias.material, DeliveryItem.Status.CONFIRMED

    line = _tokens(text)
    best, best_score = None, 0.0
    for m in Material.objects.exclude(status='archived'):
        name = _tokens(m.name)
        if not name:
            continue
        score = len(line & name) / len(name)
        if score > best_score:
            best, best_score = m, score
    if best and best_score >= MIN_SIMILARITY:
        return best, DeliveryItem.Status.AUTO_IDENTIFIED
    return None, DeliveryItem.Status.UNIDENTIFIED


def auto_match(items):
    """Подобрать материал для несопоставленных строк. Возвращает число сопоставленных."""
    found = 0
    for item in items:
        material, status = find_material(item.original_text)
        if material:
            item.material, item.matching_status = material, status
            item.save(update_fields=['material', 'matching_status'])
            found += 1
    return found


def confirm(item, material, remember=True):
    """Инженер выбрал материал вручную. remember — запомнить написание как синоним."""
    item.material = material
    item.matching_status = DeliveryItem.Status.CONFIRMED
    item.save(update_fields=['material', 'matching_status'])
    if remember and item.original_text.strip():
        MaterialAlias.objects.update_or_create(
            normalized=MaterialAlias.normalize(item.original_text),
            defaults={'material': material, 'text': item.original_text.strip()})


def parse_lines(text):
    """Строки накладной из текста: «название; количество; ед. изм.» (разделитель ; или табуляция —
    так вставляются строки, скопированные из Excel). Возвращает [(название, количество, ед)] и ошибки."""
    rows, errors = [], []
    for n, raw in enumerate((text or '').splitlines(), start=1):
        if not raw.strip():
            continue
        parts = [p.strip() for p in re.split(r'[;\t]', raw)]
        if len(parts) < 2:
            errors.append(f'Строка {n}: нужно «название; количество; ед. изм.»')
            continue
        try:
            qty = Decimal(parts[1].replace(' ', '').replace(',', '.'))
        except InvalidOperation:
            errors.append(f'Строка {n}: «{parts[1]}» — не число')
            continue
        rows.append((parts[0], qty, parts[2] if len(parts) > 2 else ''))
    return rows, errors
