"""
Проверка документов о качестве по реестру Росаккредитации.

    python manage.py registry_check
        перепроверить все сертификаты и декларации в базе (кроме отказных писем и добровольных),
        которые ещё действуют. Удобно запускать раз в неделю через Планировщик заданий Windows.

    python manage.py registry_check "РОСС RU С-RU.АЩ01.В.00760/25"
    python manage.py registry_check --url https://pub.fsa.gov.ru/rss/certificate/view/3627218
        найти один документ и показать, что вернул реестр.
        --raw — дополнительно сохранить сырой ответ реестра в registry_debug.json
                (пригодится, если реестр изменился и поля перестали заполняться).
"""

import json
from datetime import date

from django.core.management.base import BaseCommand
from django.db.models import Q

from core import cert_jobs, fsa_registry
from core.models import Certificate


class Command(BaseCommand):
    help = 'Проверка сертификатов и деклараций по реестру Росаккредитации'

    def add_arguments(self, parser):
        parser.add_argument('number', nargs='?', help='номер документа')
        parser.add_argument('--url', help='ссылка на карточку в реестре (из QR-кода)')
        parser.add_argument('--raw', action='store_true', help='сохранить сырой ответ в registry_debug.json')

    def handle(self, *args, number=None, url=None, raw=False, **options):
        if number or url:
            return self._one(number, url, raw)

        certs = Certificate.objects.exclude(document_type=Certificate.DocType.REFUSAL) \
            .filter(Q(expiry_date__isnull=True) | Q(expiry_date__gte=date.today())) \
            .prefetch_related('materialcertificate_set__material')
        certs = [c for c in certs if cert_jobs.certificate_checkable(c)]
        self.stdout.write(f'Проверяю документов: {len(certs)}')
        problems = 0
        for cert in certs:
            res = cert_jobs.recheck_certificate(cert)
            if res['state'] == 'unavailable':
                self.stderr.write(f'Реестр недоступен: {res["message"]}. Проверка остановлена.')
                return
            subject = cert.materials_label()
            line = f'{cert}{f" ({subject})" if subject else ""}: '
            if res['state'] == 'found':
                line += res['data']['status_text']
                if res.get('diffs'):
                    line += ' | расхождения: ' + '; '.join(f'{l}: у нас {a}, в реестре {b}' for l, a, b in res['diffs'])
                bad = res['data']['status'] != 'active' or res.get('diffs')
            else:
                line += 'не найден в реестре'
                bad = True
            problems += bool(bad)
            self.stdout.write(self.style.WARNING(line) if bad else line)
        self.stdout.write(self.style.SUCCESS(f'Готово. Требуют внимания: {problems}.'))

    def _one(self, number, url, raw):
        try:
            data = fsa_registry.lookup(number=number, registry_url=url, raw=raw)
        except fsa_registry.RegistryUnavailable as e:
            self.stderr.write(f'Реестр недоступен: {e}')
            return
        if data is None:
            self.stdout.write('В реестре не найден.')
            return
        if raw:
            with open('registry_debug.json', 'w', encoding='utf-8') as f:
                json.dump({'item': data.pop('raw_item'), 'card': data.pop('raw_card')}, f,
                          ensure_ascii=False, indent=2, default=str)
            self.stdout.write('Сырой ответ сохранён в registry_debug.json')
        for key, value in data.items():
            self.stdout.write(f'{key:14}: {value}')
