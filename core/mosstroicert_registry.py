import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

BASE_URL = 'https://mosstroicert.ru'
REGISTRY_URL = BASE_URL + '/reestr-sertifikatov/'
CARD_URL = BASE_URL + '/index.php?route=registries/registries/findOsCertificate'
SEARCH_URL = BASE_URL + '/index.php?route=registries/registries/getCertificatesFromOsCertificates'
PROVIDER = 'mosstroicert'
SOURCE_LABEL = 'Реестр Мосстройсертификации'
USER_AGENT = 'StroyControl/1.0 (document verification)'


class SourceUnavailable(Exception):
    pass


class AmbiguousDocument(Exception):
    pass


def applies_to(number):
    return bool(re.match(r'^\s*RU\.(?:MCC|МСС)\.', number or '', re.I))


def normalize_number(number):
    return re.sub(r'^(\s*RU\.)MCC', lambda match: match.group(1) + 'МСС',
                  (number or '').strip(), flags=re.I)


def _date(value):
    try:
        return datetime.strptime(value.strip(), '%d.%m.%Y').date()
    except (ValueError, AttributeError):
        return None


def _status(value):
    lowered = (value or '').lower()
    if 'действ' in lowered:
        return 'active'
    if 'приостан' in lowered:
        return 'suspended'
    if any(word in lowered for word in ('заверш', 'аннул', 'прекращ', 'недейств', 'архив')):
        return 'ended'
    return ''


def _lines(fragment):
    fragment = re.sub(r'<!--.*?-->', '', fragment, flags=re.S)
    fragment = re.sub(r'</(?:div|p|span|td|tr)>', '\n', fragment, flags=re.I)
    fragment = re.sub(r'<[^>]+>', ' ', fragment)
    return [line for line in (re.sub(r'\s+', ' ', html.unescape(part)).strip()
                              for part in fragment.splitlines()) if line]


def _rows(template):
    rows = []
    for row_html in re.findall(r'<tr class="reestr__row">(.*?)</tr>', template, re.S | re.I):
        cells = re.findall(r'<td class="reestr__td[^>]*>(.*?)</td>', row_html, re.S | re.I)
        if len(cells) >= 6:
            rows.append([_lines(cell) for cell in cells])
    return rows


def _org(lines):
    if not lines or not lines[0]:
        return {}
    return {'short_name': lines[0], 'full_name': lines[0], 'inn': '', 'ogrn': '', 'address': ''}


def _normalize(row):
    number_status, product_manufacturer, owner, issuer, dates = row[1:6]
    number = number_status[0] if number_status else ''
    status_text = number_status[1] if len(number_status) > 1 else ''
    status = _status(status_text)
    product = product_manufacturer[0] if product_manufacturer else ''
    manufacturer = product_manufacturer[1] if len(product_manufacturer) > 1 else ''
    issuer_name = issuer[0] if issuer else ''
    issuer_number = issuer[1] if len(issuer) > 1 else ''
    return {
        'kind': 'certificate',
        'url': REGISTRY_URL,
        'number': number,
        'issue_date': _date(dates[0]) if dates else None,
        'expiry_date': _date(dates[1]) if len(dates) > 1 else None,
        'status': status,
        'status_text': status_text or {'active': 'Действует', 'suspended': 'Приостановлен',
                                      'ended': 'Прекращён'}.get(status, 'неизвестен'),
        'manufacturer': _org([manufacturer]),
        'applicant': _org(owner),
        'product': product,
        'product_items': [product] if product else [],
        'standards': [],
        'provider': PROVIDER,
        'source_label': SOURCE_LABEL,
        'issuer': ', '.join(value for value in (issuer_name, issuer_number) if value),
    }


def _request(number, timeout):
    body = urllib.parse.urlencode({
        'number_certificate': number,
        'date_begin': '', 'date_end': '', 'owner_name': '', 'title': '', 'limit': 20, 'page': 1,
    }).encode('utf-8')
    request = urllib.request.Request(SEARCH_URL, data=body, headers={
        'User-Agent': USER_AGENT,
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json, text/plain, */*',
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as e:
        raise SourceUnavailable(f'Мосстройсертификация вернула код {e.code}') from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SourceUnavailable(f'Мосстройсертификация недоступна: {e}') from e

    try:
        payload = json.loads(raw.decode('utf-8'))
        return int(payload.get('total', 0)), payload.get('template') or ''
    except (ValueError, TypeError, AttributeError) as e:
        raise SourceUnavailable('Мосстройсертификация вернула некорректный ответ') from e


def _with_record_id(row, record_id):
    result = _normalize(row)
    result['registry_record_id'] = record_id
    result['url'] = CARD_URL + '?' + urllib.parse.urlencode({'os_certificate_id': record_id})
    return result


def lookup(number, timeout=15):
    if not applies_to(number):
        return None
    total, template = _request(normalize_number(number), timeout)
    if total == 0:
        return None
    if total > 1:
        raise AmbiguousDocument('В реестре Мосстройсертификации несколько сертификатов с этим номером.')

    rows = _rows(template)
    record_ids = re.findall(r'data-os_certificate_id="(\d+)"', template)
    if len(rows) != 1 or len(record_ids) != 1:
        raise SourceUnavailable('Не удалось разобрать ответ Мосстройсертификации: изменилась разметка')
    return _with_record_id(rows[0], record_ids[0])


def search_candidates(number, limit=5, timeout=15):
    if not number:
        return []
    total, template = _request(number, timeout)
    if total == 0:
        return []
    rows = _rows(template)
    record_ids = re.findall(r'data-os_certificate_id="(\d+)"', template)
    if not rows or len(rows) != len(record_ids):
        raise SourceUnavailable('Не удалось разобрать ответ Мосстройсертификации: изменилась разметка')
    return [_with_record_id(row, record_id) for row, record_id in zip(rows[:limit], record_ids[:limit])]
