"""
core/events.py

Запись в журнал событий. Вызывать после действия, которое важно потом найти:
    log_event(request, 'Сформирован Excel', act, 'АОСР_№1.xlsx')
Ошибка записи в журнал никогда не ломает основное действие.
"""

import logging

logger = logging.getLogger(__name__)


def log_event(request, action, obj=None, details=''):
    from .models import EventLog
    try:
        user = getattr(request, 'user', None)
        EventLog.objects.create(
            user=user if user is not None and user.is_authenticated else None,
            action=action[:100],
            object_repr=str(obj)[:300] if obj is not None else '',
            details=details or '',
        )
    except Exception:
        logger.exception('Не удалось записать событие «%s»', action)
