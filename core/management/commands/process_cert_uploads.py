"""Обработка постоянной очереди распознавания документов о качестве."""

import logging
import time

from django.core.management.base import BaseCommand

from core import cert_jobs

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Обрабатывает очередь распознавания сертификатов в отдельном процессе'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='обработать одну задачу и завершиться')
        parser.add_argument('--poll-interval', type=float, default=2.0,
                            help='пауза между проверками очереди в секундах (по умолчанию 2)')

    def handle(self, *args, **options):
        interval = options['poll_interval']
        if interval <= 0:
            self.stderr.write('Интервал должен быть больше нуля.')
            return

        if options['once']:
            recovered = cert_jobs.recover_stale()
            if cert_jobs.process_next():
                self.stdout.write(self.style.SUCCESS('Задача обработана.'))
            else:
                self.stdout.write('Очередь пуста.')
            if recovered:
                self.stdout.write(f'Возвращено в очередь после перерыва: {recovered}.')
            return

        self.stdout.write('Worker распознавания запущен. Остановка: Ctrl+C.')
        try:
            while True:
                recovered = cert_jobs.recover_stale()
                if recovered:
                    logger.info('Возвращено в очередь после перерыва: %s', recovered)
                if not cert_jobs.process_next():
                    time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write('Worker остановлен.')
