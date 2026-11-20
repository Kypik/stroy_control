"""
core/fsa_registry.py

Проверка сертификатов и деклараций по реестру Росаккредитации (pub.fsa.gov.ru).

Официального бесплатного API нет, поэтому программа делает то же, что сайт реестра в браузере:
анонимный вход → поиск по номеру (или сразу карточка по ссылке из QR-кода) → карточка документа.
Сайт отвечает только на запросы из России. Если он изменится — поправить нужно только этот файл;
сырой ответ можно посмотреть командой  python manage.py registry_check "номер" --raw

Модуль не зависит от Django и использует только стандартную библиотеку Python.

Главная функция:
    lookup(number='РОСС RU С-RU.АЩ01.В.00760/25', registry_url='https://pub.fsa.gov.ru/rss/certificate/view/3627218')
    -> словарь с данными из реестра, None (не найден) или исключение RegistryUnavailable
"""

import base64
import http.cookiejar
import json
import logging
import os
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

logger = logging.getLogger(__name__)

BASE = 'https://pub.fsa.gov.ru'
ANONYMOUS = {
    'username': os.getenv('FSA_USERNAME', 'anonymous'),
    'password': os.getenv('FSA_PASSWORD', ''),
}   # общий вход сайта реестра; пароль задаётся в .env, не в коде
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36')

KINDS = {
    'certificate': {
        'page': '/rss/certificate',
        'search': '/api/v1/rss/common/certificates/get',
        'card': '/api/v1/rss/common/certificates/{id}',
        'view': BASE + '/rss/certificate/view/{id}',
    },
    'declaration': {
        'page': '/rds/declaration',
        'search': '/api/v1/rds/common/declarations/get',
        'card': '/api/v1/rds/common/declarations/{id}',
        'view': BASE + '/rds/declaration/view/{id}',
    },
}

# коды статусов реестра → наши
STATUS_BY_ID = {6: 'active', 3: 'active', 14: 'ended', 1: 'ended', 11: 'ended', 15: 'suspended'}
STATUS_TEXT = {'active': 'действует', 'ended': 'прекращён', 'suspended': 'приостановлен'}


class RegistryUnavailable(Exception):
    """Реестр не ответил: нет интернета, не российский IP, сайт лежит или изменился."""


# ---------------------------------------------------------------------------
# HTTP-сессия
# ---------------------------------------------------------------------------

class _Session:
    def __init__(self, timeout=20, allow_unverified_ssl=False):
        self.timeout = timeout
        self.allow_unverified_ssl = allow_unverified_ssl
        self.jar = http.cookiejar.CookieJar()
        self.token = ''
        self.expires = 0
        self._build(verify=True)

    def _build(self, verify):
        handlers = [urllib.request.HTTPCookieProcessor(self.jar)]
        if not verify:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            handlers.append(urllib.request.HTTPSHandler(context=ctx))
        self.opener = urllib.request.build_opener(*handlers)
        self.verify = verify

    def _xsrf(self):
        for c in self.jar:
            if c.name.upper() == 'XSRF-TOKEN':
                return urllib.parse.unquote(c.value)
        return ''

    def request(self, method, path, body=None, auth=True, html=False):
        headers = {
            'User-Agent': UA,
            'Accept': 'text/html,*/*' if html else 'application/json, text/plain, */*',
            'Accept-Language': 'ru-RU,ru;q=0.9',
            'Origin': BASE,
            'Referer': BASE + '/rss/certificate',
            'lkId': '', 'orgId': '',
            'Cache-Control': 'no-cache', 'Pragma': 'no-cache',
        }
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if self._xsrf():
            headers['X-XSRF-TOKEN'] = self._xsrf()
        if auth:
            headers['Authorization'] = f'Bearer {self.token}'
        data = json.dumps(body).encode('utf-8') if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
        try:
            resp = self.opener.open(req, timeout=self.timeout)
            return resp.status, resp.headers, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLError) and self.verify and self.allow_unverified_ssl:
                # у государственных сайтов бывает сертификат Минцифры, которого нет в Windows
                logger.warning('Реестр: сертификат сайта не проверен (%s), повторяю без проверки', e.reason)
                self._build(verify=False)
                return self.request(method, path, body, auth, html)
            raise RegistryUnavailable(f'Сайт реестра не отвечает: {e.reason}')
        except (TimeoutError, OSError) as e:
            raise RegistryUnavailable(f'Сайт реестра не отвечает: {e}')

    def login(self):
        if not ANONYMOUS['password']:
            raise RegistryUnavailable('Не задан пароль реестра: добавьте FSA_PASSWORD в .env')
        if self.token and time.time() < self.expires:
            return
        self.request('GET', '/rss/certificate', auth=False, html=True)   # куки сессии и XSRF
        status, headers, raw = self.request('POST', '/login', body=ANONYMOUS, auth=False)
        token = (headers.get('Authorization') or '').replace('Bearer', '').strip() if headers else ''
        if status != 200 or not token:
            hint = ' (сайт отвечает только на запросы из России)' if status in (401, 403) else ''
            raise RegistryUnavailable(f'Реестр не пустил: вход вернул код {status}{hint}')
        self.token = token
        self.expires = _jwt_exp(token) - 60

    def api(self, method, path, body=None):
        self.login()
        status, _, raw = self.request(method, path, body)
        if status == 401:                       # токен истёк раньше срока — входим заново
            self.token = ''
            self.login()
            status, _, raw = self.request(method, path, body)
        if status == 400:
            try:
                error = json.loads(raw.decode('utf-8'))
                detail = error.get('detail') or error.get('message') or error.get('title')
            except (ValueError, AttributeError):
                detail = ''
            raise RegistryUnavailable(f'Реестр отклонил запрос (400): {detail or "ошибка валидации"}')
        if status == 404:
            return None
        if status != 200:
            raise RegistryUnavailable(f'Реестр ответил кодом {status} на {path}')
        try:
            return json.loads(raw.decode('utf-8'))
        except ValueError:
            raise RegistryUnavailable('Реестр вернул не данные, а страницу — возможно, сайт изменился')


def _jwt_exp(token):
    try:
        payload = token.split('.')[1]
        payload += '=' * (-len(payload) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload))['exp'])
    except Exception:
        return time.time() + 3600


_shared = {}


def _session(timeout, allow_unverified_ssl):
    """Одна сессия на процесс — чтобы не входить в реестр на каждый документ."""
    key = (timeout, allow_unverified_ssl)
    if key not in _shared:
        _shared[key] = _Session(timeout, allow_unverified_ssl)
    return _shared[key]


# ---------------------------------------------------------------------------
# Разбор ответов
# ---------------------------------------------------------------------------

_LAT_TO_CYR = str.maketrans('ABCEHKMOPTXY', 'АВСЕНКМОРТХУ')


def number_key(number):
    normalized = re.sub(r'[_–—−]', '-', number or '')
    return re.sub(r'[\s№N]+', '', normalized.upper().translate(_LAT_TO_CYR))


def kind_of_number(number):
    """Сертификат или декларация — по номеру. None, если не понять."""
    key = number_key(number)
    if re.search(r'Д-|\.Д\d', key):
        return 'declaration'
    if re.search(r'С-|\.[ВН]\d|\.А\d', key):
        return 'certificate'
    return None


def parse_registry_url(url):
    """('certificate'|'declaration', id) из ссылки реестра (обычно из QR-кода)."""
    m = re.search(r'pub\.fsa\.gov\.ru/(rss/certificate|rds/declaration)/view/(\d+)', url or '')
    if not m:
        return None, None
    return ('certificate' if m.group(1).startswith('rss') else 'declaration'), m.group(2)


def _first(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, '', []):
            return d[k]
    return None


def _date(value):
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000 if value > 1e11 else value).date()
    s = str(value).strip()
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return date(*map(int, m.groups()))
    m = re.match(r'(\d{2})\.(\d{2})\.(\d{4})', s)
    if m:
        d, mth, y = map(int, m.groups())
        return date(y, mth, d)
    return None


def _status(*values):
    for v in values:
        if v in (None, ''):
            continue
        if isinstance(v, dict):
            v = v.get('id') or v.get('name')
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit()):
            if int(v) in STATUS_BY_ID:
                return STATUS_BY_ID[int(v)]
            continue
        s = str(v).lower()
        if 'приостан' in s:
            return 'suspended'
        if any(w in s for w in ('прекращ', 'архив', 'недейств', 'аннул')):
            return 'ended'
        if 'действ' in s or 'возобнов' in s:
            return 'active'
    return ''


def _org(o):
    if not isinstance(o, dict):
        return {}
    address = ''
    for a in o.get('addresses') or []:
        address = (a or {}).get('fullAddress') or (a or {}).get('address') or ''
        if address:
            break
    return {
        'short_name': (o.get('shortName') or o.get('fullName') or '').strip(),
        'full_name': (o.get('fullName') or '').strip(),
        'inn': str(o.get('inn') or '').strip(),
        'ogrn': str(o.get('ogrn') or o.get('ogrnip') or '').strip(),
        'address': (address or o.get('address') or '').strip(),
    }


STANDARD_RE = re.compile(r'(?:ГОСТ(?:\s+Р)?(?:\s+ИСО|\s+EN|\s+ISO)?|СП|СТО|ТУ)\s+[\dA-ZА-Я][\w.\-–/]*\d', re.U)


def _standards(obj):
    text = json.dumps(obj, ensure_ascii=False) if obj else ''
    found = []
    for m in STANDARD_RE.finditer(text):
        s = re.sub(r'\s+', ' ', m.group(0)).replace('–', '-')
        if s not in found:
            found.append(s)
    return found


def _card_standards(product):
    """ГОСТы из карточки: product.identifications[].standards[].designation."""
    found = []
    for ident in product.get('identifications') or []:
        for st in (ident or {}).get('standards') or []:
            d = re.sub(r'\s+', ' ', (st or {}).get('designation') or '').strip()
            if d and d not in found:
                found.append(d)
    return found


def normalize(kind, item, card, record_id):
    """Ответ реестра → наш словарь. item — строка поиска, card — карточка (любой может быть пустым)."""
    item, card = item or {}, card or {}
    product = card.get('product') or {}
    names = [((i or {}).get('name') or '').strip() for i in product.get('identifications') or []]
    names = [n for n in names if n]
    product_name = (product.get('fullName') or _first(item, 'productFullName', 'productName', 'prodName') or '').strip()
    manufacturer = _org(card.get('manufacturer')) or {}
    if not manufacturer.get('short_name') and item.get('manufacterName'):   # так в реестре, с опечаткой
        manufacturer = {'short_name': item['manufacterName'], 'full_name': item['manufacterName'],
                        'inn': '', 'ogrn': '', 'address': ''}
    status = _status(card.get('idStatus'), item.get('idStatus'), card.get('status'), item.get('status'))
    return {
        'kind': kind,
        'id': str(record_id),
        'url': KINDS[kind]['view'].format(id=record_id),
        'number': (_first(card, 'number', 'declNumber', 'certNumber') or _first(item, 'number') or '').strip(),
        'issue_date': _date(_first(card, 'certRegDate', 'declRegDate', 'regDate', 'date')
                            or _first(item, 'date', 'regDate', 'certRegDate', 'declDate')),
        'expiry_date': _date(_first(card, 'certEndDate', 'declEndDate', 'endDate')
                             or _first(item, 'endDate', 'certEndDate', 'declEndDate')),
        'status': status,
        'status_text': STATUS_TEXT.get(status, 'неизвестен'),
        'manufacturer': manufacturer,
        'applicant': _org(card.get('applicant')),
        'product': product_name,
        'product_items': names,
        'standards': _card_standards(product) or _standards(product) or _standards(item.get('productFullName')),
    }


# ---------------------------------------------------------------------------
# Поиск
# ---------------------------------------------------------------------------

def _search_bodies(kind, number):
    """Тела запросов в формате текущего сайта реестра."""
    if kind == 'declaration':
        dates = {'regDate': {'minDate': '', 'maxDate': ''}, 'endDate': {'minDate': '', 'maxDate': ''}}
        yield {'size': 10, 'page': 0,
               'filter': dict(dates, columnsSearch=[{'name': 'number', 'search': number, 'type': 0}]),
               'columnsSort': [{'column': 'declDate', 'sort': 'DESC'}]}
        return

    dates = {'regDate': {'startDate': None, 'endDate': None}, 'endDate': {'startDate': None, 'endDate': None}}
    columns = [{'column': 'number', 'search': number}]
    for extra in ({'idCertScheme': []}, {}):
        yield {'size': 10, 'page': 0, 'filter': dict(extra, **dates, columnsSearch=columns),
               'columnsSort': [{'column': 'date', 'sort': 'DESC'}]}


def _search(sess, kind, number, exact=True):
    key = number_key(number)
    last_error = None
    for body in _search_bodies(kind, number):
        try:
            data = sess.api('POST', KINDS[kind]['search'], body) or {}
        except RegistryUnavailable as e:   # 400 — сайт не принял такой вариант запроса, пробуем следующий
            last_error = e
            continue
        items = data.get('items') or []
        if not exact:
            return items
        exact_items = [i for i in items if number_key(i.get('number')) == key]
        return exact_items[0] if exact_items else None
    raise last_error


def lookup(number=None, registry_url=None, timeout=20, allow_unverified_ssl=False, raw=False):
    """
    Найти документ в реестре. Сначала по ссылке из QR-кода (точнее всего), иначе по номеру.
    Возвращает словарь (см. normalize) или None, если документа в реестре нет.
    RegistryUnavailable — реестр не ответил, проверить не получилось.
    raw=True — добавить в ответ сырые данные реестра (ключи 'raw_item', 'raw_card').
    """
    sess = _session(timeout, allow_unverified_ssl)
    kind, record_id = parse_registry_url(registry_url)
    item = None

    if not record_id:
        if not number:
            return None
        kinds = [kind_of_number(number)] if kind_of_number(number) else ['certificate', 'declaration']
        for k in kinds:
            item = _search(sess, k, number)
            if item:
                kind = k
                record_id = item.get('id') or item.get('idCertificate') or item.get('idDeclaration')
                break
        if not item:
            return None

    card = sess.api('GET', KINDS[kind]['card'].format(id=record_id))
    if card is None and item is None:
        return None
    result = normalize(kind, item, card, record_id)
    if raw:
        result['raw_item'], result['raw_card'] = item, card
    return result


def search_candidates(number, limit=5, timeout=20, allow_unverified_ssl=False):
    """Найти несколько документов по фрагменту номера и получить их карточки."""
    sess = _session(timeout, allow_unverified_ssl)
    kinds = [kind_of_number(number)] if kind_of_number(number) else ['certificate', 'declaration']
    result = []
    seen = set()
    for kind in kinds:
        for item in _search(sess, kind, number, exact=False):
            record_id = item.get('id') or item.get('idCertificate') or item.get('idDeclaration')
            if not record_id or (kind, str(record_id)) in seen:
                continue
            card = sess.api('GET', KINDS[kind]['card'].format(id=record_id))
            try:
                normalized = normalize(kind, item, card, record_id)
            except (AttributeError, TypeError, ValueError) as e:
                logger.warning('Пропущена некорректная карточка ФГИС %s: %s', record_id, e)
                continue
            normalized['source_label'] = 'ФГИС Росаккредитации'
            result.append(normalized)
            seen.add((kind, str(record_id)))
            if len(result) >= limit:
                return result
    return result


def compare(recognized, registry):
    """Что на скане прочитано не так, как в реестре: [(поле, со скана, из реестра)]."""
    diffs = []

    def fmt(d):
        if isinstance(d, str):
            d = _date(d)
        return d.strftime('%d.%m.%Y') if d else '—'

    if number_key(recognized.get('number')) != number_key(registry.get('number')) and registry.get('number'):
        diffs.append(('Номер', recognized.get('number') or '—', registry['number']))
    for field, label in (('issue_date', 'Дата выдачи'), ('expiry_date', 'Действует до')):
        a, b = _date(recognized.get(field)), _date(registry.get(field))
        if b and a != b:
            diffs.append((label, fmt(a), fmt(b)))
    inn_a = (recognized.get('manufacturer') or {}).get('inn')
    inn_b = (registry.get('manufacturer') or {}).get('inn')
    if inn_b and inn_a != inn_b:
        diffs.append(('ИНН изготовителя', inn_a or '—', inn_b))
    return diffs
