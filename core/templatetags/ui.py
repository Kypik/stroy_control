"""Шаблонные теги интерфейса: «?»-подсказки."""

from django import template

from core.help_texts import HELP_TEXTS

register = template.Library()


@register.inclusion_tag('core/_help.html')
def help_pop(key, uid=''):
    """Подсказка по ключу из core.help_texts.HELP_TEXTS."""
    return {
        'text': HELP_TEXTS.get(key, ''),
        'uid': uid or key.replace('.', '-'),
        'label': 'Пояснение',
    }


@register.inclusion_tag('core/_help.html')
def help_pop_text(text, uid):
    """Подсказка из готового текста (например, help_text поля формы)."""
    return {'text': text or '', 'uid': uid, 'label': 'Пояснение'}
