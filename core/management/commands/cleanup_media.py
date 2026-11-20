"""
Удаляет старые и ненужные файлы из media/:
- generated_acts/preview/ — кэш предпросмотра (регенерируется сам при открытии страницы);
- generated_acts/*.xlsx и *.pdf — сгенерированные акты старше --days дней (формируются заново из базы);
- сироты в documents/, cert_uploads/, generated_acts/corrected/ — файлы без записи в базе
  (не привязаны к документам или исправленным файлам актов).

Запуск:
    python manage.py cleanup_media --dry-run     # только показать, что будет удалено
    python manage.py cleanup_media               # удалить файлы старше 30 дней и сирот
    python manage.py cleanup_media --days 7      # свой порог
"""

import os
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Удаляет старые сгенерированные файлы, кэш предпросмотра и файлы-сироты из media/'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30,
                            help='сгенерированные акты старше стольких дней удалять (по умолчанию 30)')
        parser.add_argument('--dry-run', action='store_true', help='только показать, что будет удалено')

    def handle(self, *args, **options):
        from core.models import Documents, HiddenWorkAct

        media = Path(settings.MEDIA_ROOT)
        cutoff = timezone.now() - timedelta(days=options['days'])
        dry = options['dry_run']
        deleted = []

        def remove(path, reason):
            if not path.is_file():
                return
            deleted.append((path, reason))
            if not dry:
                path.unlink()

        # 1. Сгенерированные акты (xlsx и их pdf) старше порога — формируются заново из базы
        generated = media / 'generated_acts'
        if generated.is_dir():
            for path in generated.iterdir():
                if path.suffix in ('.xlsx', '.pdf') and self._mtime(path) < cutoff:
                    remove(path, f'старше {options["days"]} дней')

        # 2. Кэш предпросмотра: старше суток или акт уже удалён
        preview = generated / 'preview'
        if preview.is_dir():
            act_ids = set(HiddenWorkAct.objects.values_list('pk', flat=True))
            for act_dir in preview.iterdir():
                try:
                    act_id = int(act_dir.name.removeprefix('act_'))
                except ValueError:
                    act_id = None
                for path in act_dir.rglob('*'):
                    if path.is_file() and (act_id not in act_ids or self._mtime(path) < timezone.now() - timedelta(days=1)):
                        remove(path, 'кэш предпросмотра')

        # 3. Файлы-сироты: на диске есть, в базе нет
        referenced = set(Documents.objects.values_list('path_to_file', flat=True))
        corrected = set(HiddenWorkAct.objects.exclude(corrected_file='')
                        .values_list('corrected_file', flat=True))
        for folder in ('documents', 'cert_uploads', os.path.join('generated_acts', 'corrected')):
            root = media / folder
            if not root.is_dir():
                continue
            for path in root.rglob('*'):
                if path.is_file() and path.relative_to(media).as_posix() not in referenced | corrected:
                    remove(path, 'нет записи в базе')

        for path, reason in deleted:
            self.stdout.write(f"{'[dry-run] ' if dry else ''}{path.relative_to(media)} — {reason}")
        self.stdout.write(self.style.SUCCESS(f"{'Будет удалено' if dry else 'Удалено'} файлов: {len(deleted)}"))

    @staticmethod
    def _mtime(path):
        from datetime import datetime
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.get_current_timezone())
