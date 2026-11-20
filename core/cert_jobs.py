"""Постоянная очередь распознавания документов в CertificateUpload.

Сайт только ставит загрузки в очередь. Отдельный процесс
``python manage.py process_cert_uploads`` выполняет OCR и проверку реестров.
"""

import logging
import os
import re
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db import close_old_connections, models, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

RECOGNITION_ERROR = 'Не удалось распознать файл. Запустите распознавание заново или введите данные вручную.'


def preview_dir(upload_id):
    return os.path.join(settings.MEDIA_ROOT, 'cert_uploads', str(upload_id))


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _stale_before():
    seconds = getattr(settings, 'CERT_JOB_STALE_SECONDS', 300)
    return timezone.now() - timedelta(seconds=seconds)


def _set_progress(upload_id, text):
    from .models import CertificateUpload

    CertificateUpload.objects.filter(pk=upload_id).update(
        progress=text, worker_heartbeat=timezone.now())


def recover_stale():
    """Вернуть в очередь задачи, от которых давно нет сигнала worker."""
    from .models import CertificateUpload

    return CertificateUpload.objects.filter(status=CertificateUpload.Status.PROCESSING).filter(
        models.Q(worker_heartbeat__isnull=True) | models.Q(worker_heartbeat__lt=_stale_before())
    ).update(status=CertificateUpload.Status.QUEUED, progress='', worker_heartbeat=None)


def claim_next():
    """Атомарно занять одну задачу; skip_locked не даёт worker'ам взять её одновременно."""
    from .models import CertificateUpload

    with transaction.atomic():
        upload = CertificateUpload.objects.select_for_update(skip_locked=True).filter(
            status=CertificateUpload.Status.QUEUED).order_by('created_at', 'pk').first()
        if upload is None:
            return None
        upload.status = CertificateUpload.Status.PROCESSING
        upload.error = ''
        upload.progress = ''
        upload.worker_heartbeat = timezone.now()
        upload.save(update_fields=['status', 'error', 'progress', 'worker_heartbeat'])
        return upload.pk


def _run(upload_id):
    from .cert_recognition import recognize_file
    from .models import CertificateUpload

    try:
        upload = CertificateUpload.objects.select_related('document').get(pk=upload_id)
        path = upload.document.path_to_file.path
        result = recognize_file(
            path, name=path,
            preview_dir=preview_dir(upload_id),
            on_progress=lambda text: _set_progress(upload_id, text),
            tesseract_cmd=getattr(settings, 'TESSERACT_CMD', None),
        )
        reachable = True
        for doc in result['documents']:
            if reachable and registry_applicable(doc):
                _set_progress(upload_id, 'Проверяю в реестре Росаккредитации…')
                doc['registry'] = check_in_registry(doc)
                reachable = doc['registry']['state'] != 'unavailable'
            elif registry_applicable(doc):
                doc['registry'] = {
                    'state': 'unavailable',
                    'message': 'Реестр не ответил на предыдущий запрос. Попробуйте проверить документ позже.',
                }

        result = _jsonable(result)
        result['saved'] = {}
        if upload.result.get('material_id'):
            result['material_id'] = upload.result['material_id']

        document = upload.document
        document.text = '\n\n'.join(d.get('text', '') for d in result['documents'])
        document.save(update_fields=['text'])
        CertificateUpload.objects.filter(pk=upload_id).update(
            status=CertificateUpload.Status.DONE, progress='', error='',
            worker_heartbeat=None, result=result)
    except Exception:
        logger.exception('Ошибка распознавания загрузки %s', upload_id)
        CertificateUpload.objects.filter(pk=upload_id).update(
            status=CertificateUpload.Status.ERROR, error=RECOGNITION_ERROR,
            progress='', worker_heartbeat=None)
    finally:
        close_old_connections()


def process_next():
    """Обработать одну задачу. False означает, что очередь пуста."""
    upload_id = claim_next()
    if upload_id is None:
        return False
    _run(upload_id)
    return True


def registry_applicable(doc):
    """Есть ли смысл искать документ в реестрах: обязательный документ или номер Мосстройсертификации."""
    from . import mosstroicert_registry
    from .cert_recognition import DOC_CERTIFICATE, DOC_DECLARATION
    if not getattr(settings, 'FSA_REGISTRY_ENABLED', True):
        return False
    voluntary_allowed = mosstroicert_registry.applies_to(doc.get('number'))
    return (doc.get('document_type') in (DOC_CERTIFICATE, DOC_DECLARATION)
            and (not doc.get('voluntary') or voluntary_allowed)
            and bool(doc.get('number') or doc.get('registry_url')))


def check_in_registry(doc, number=None, allow_fuzzy=True):
    """Сначала точные варианты в ФГИС и Мосстройсертификации, затем кандидаты по числовому хвосту."""
    from . import fsa_registry, mosstroicert_registry
    from .document_numbers import numeric_tail, variants

    registry_url = doc.get('registry_url') if number is None else None
    number = number if number is not None else doc.get('number')
    checked_at = datetime.now().isoformat(timespec='seconds')
    if not number and not registry_url:
        return {'state': 'not_found', 'message': 'Введите номер документа.', 'checked_at': checked_at}

    recognized = dict(doc, number=number)
    errors = []

    def found(data):
        return _jsonable({'state': 'found', 'message': '', 'data': data,
                          'diffs': fsa_registry.compare(recognized, data), 'checked_at': checked_at})

    for index, candidate in enumerate(variants(number) or ['']):
        try:
            data = fsa_registry.lookup(
                number=candidate, registry_url=registry_url if index == 0 else None,
                timeout=getattr(settings, 'FSA_TIMEOUT', 15),
                allow_unverified_ssl=getattr(settings, 'FSA_ALLOW_UNVERIFIED_SSL', False))
        except fsa_registry.RegistryUnavailable as e:
            logger.warning('ФГИС недоступна: %s', e)
            data = None
            errors.append('ФГИС временно недоступна.')
        except Exception:
            logger.exception('Ошибка разбора ответа ФГИС')
            data = None
            errors.append('Не удалось обработать ответ ФГИС.')
        if data is not None:
            return found(data)

        if mosstroicert_registry.applies_to(candidate):
            try:
                data = mosstroicert_registry.lookup(candidate, timeout=getattr(settings, 'FSA_TIMEOUT', 15))
            except mosstroicert_registry.AmbiguousDocument as e:
                return {'state': 'ambiguous', 'message': str(e), 'checked_at': checked_at}
            except mosstroicert_registry.SourceUnavailable as e:
                logger.warning('Реестр Мосстройсертификации недоступен: %s', e)
                data = None
                errors.append('Реестр Мосстройсертификации временно недоступен.')
            except Exception:
                logger.exception('Ошибка разбора ответа Мосстройсертификации')
                data = None
                errors.append('Не удалось обработать ответ Мосстройсертификации.')
            if data is not None:
                return found(data)

    tail = numeric_tail(number)
    if allow_fuzzy and tail:
        candidates = []
        try:
            candidates.extend(fsa_registry.search_candidates(
                tail, timeout=getattr(settings, 'FSA_TIMEOUT', 15),
                allow_unverified_ssl=getattr(settings, 'FSA_ALLOW_UNVERIFIED_SSL', False)))
        except Exception:
            logger.exception('Ошибка поиска кандидатов в ФГИС')
            errors.append('Не удалось выполнить поиск похожих документов в ФГИС.')
        if mosstroicert_registry.applies_to(number):
            try:
                candidates.extend(mosstroicert_registry.search_candidates(
                    tail, timeout=getattr(settings, 'FSA_TIMEOUT', 15)))
            except Exception:
                logger.exception('Ошибка поиска кандидатов в Мосстройсертификации')
                errors.append('Не удалось выполнить поиск похожих документов в Мосстройсертификации.')
        if candidates:
            unique = list({(item.get('provider'), item['url']): item for item in candidates}.values())
            return _jsonable({'state': 'candidates',
                              'message': 'Точного совпадения нет. Выберите подходящий документ из найденных в официальных реестрах.',
                              'candidates': unique, 'checked_at': checked_at})

    if errors:
        return {'state': 'unavailable', 'message': ' '.join(dict.fromkeys(errors)), 'checked_at': checked_at}
    source_names = 'ФГИС и реестре Мосстройсертификации' if mosstroicert_registry.applies_to(number) else 'ФГИС'
    return {'state': 'not_found',
            'message': f'Документ с номером {number} не найден в {source_names}.',
            'checked_at': checked_at}


def start(upload_id):
    """Поставить файл в постоянную очередь; worker увидит её после фиксации транзакции."""
    from .models import CertificateUpload

    CertificateUpload.objects.filter(pk=upload_id).update(
        status=CertificateUpload.Status.QUEUED, error='', progress='', worker_heartbeat=None)


def state(upload):
    """Статус для страницы: (код, текст). Код: queued / processing / done / error / interrupted."""
    S = upload.Status
    if upload.status == S.QUEUED:
        return 'queued', 'В очереди — распознавание начнётся автоматически.'
    if upload.status == S.PROCESSING:
        heartbeat = upload.worker_heartbeat
        if heartbeat is None or heartbeat < _stale_before():
            return 'interrupted', 'Распознавание прервалось. Запустите заново.'
        return 'processing', upload.progress or 'Распознаётся…'
    if upload.status == S.ERROR:
        return 'error', RECOGNITION_ERROR
    return 'done', ''


VOLUNTARY_NUMBER = re.compile(r'^РОСС\s*RU\.\d{4,5}\.', re.I)


def certificate_checkable(cert):
    """Сохранённый документ, который есть смысл искать в реестре."""
    return (cert.requires_expiry(cert.document_type)
            and not VOLUNTARY_NUMBER.match((cert.document_number or '').strip()))


def recheck_certificate(cert):
    """Перепроверить сохранённый документ по реестру и записать статус. Возвращает результат проверки.
    Даты и номер в базе не меняются — расхождения только сообщаются (в diffs)."""
    doc = {
        'number': cert.document_number,
        'registry_url': cert.registry_url if 'pub.fsa.gov.ru' in (cert.registry_url or '') else '',
        'issue_date': cert.issue_date, 'expiry_date': cert.expiry_date,
        'manufacturer': {'inn': cert.manufacturer.inn} if cert.manufacturer_id else {},
    }
    res = check_in_registry(doc, allow_fuzzy=False)
    if res['state'] == 'found':
        cert.registry_status = res['data'].get('status') or ''
        cert.registry_url = res['data'].get('url') or cert.registry_url
        cert.registry_record_id = res['data'].get('registry_record_id') or ''
    elif res['state'] == 'not_found':
        cert.registry_status = cert.RegistryStatus.NOT_FOUND
    if res['state'] in ('found', 'not_found'):
        cert.registry_checked_at = timezone.now()
        cert.save(update_fields=['registry_status', 'registry_url', 'registry_record_id', 'registry_checked_at'])
    return res
