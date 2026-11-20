"""Допустимые переходы статуса акта.

Статус — это не свободный выпадающий список: у каждого состояния есть
понятные действия. Проверка выполняется на сервере, кнопки в интерфейсе
только показывают разрешённые переходы.
"""

from .models import HiddenWorkAct

DRAFT = HiddenWorkAct.Status.DRAFT
PENDING = HiddenWorkAct.Status.PENDING
PRINTING = HiddenWorkAct.Status.PRINTING
ACCEPTED = HiddenWorkAct.Status.ACCEPTED
REJECTED = HiddenWorkAct.Status.REJECTED

TRANSITIONS = {
    DRAFT: {PENDING: 'Отправить на подписание'},
    PENDING: {DRAFT: 'Вернуть в черновик', PRINTING: 'Передать на печать'},
    PRINTING: {
        PENDING: 'Вернуть на подписание',
        ACCEPTED: 'Отметить подписанным',
        REJECTED: 'Отклонить',
    },
    ACCEPTED: {},
    REJECTED: {DRAFT: 'Вернуть в работу'},
}


def is_allowed(current, target):
    """Можно ли перевести акт из current в target."""
    return str(target) in {str(value) for value in TRANSITIONS.get(current, {})}


def transition_buttons(act):
    """Кнопки действий для карточки: подпись, целевой статус и оформление."""
    current = act.status if act is not None else DRAFT
    result = []
    for target, label in TRANSITIONS.get(current, {}).items():
        result.append({
            'value': str(target),
            'label': label,
            'primary': target in (PENDING, ACCEPTED),
            'danger': target == REJECTED,
        })
    return result


def status_label(value):
    try:
        return HiddenWorkAct.Status(value).label
    except ValueError:
        return value
