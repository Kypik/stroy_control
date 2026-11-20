"""
core/readonly.py

Доступ «только просмотр» (например, для технадзора).
Пользователь из группы READ_ONLY_GROUP видит все страницы, но ничего не может сохранить.

Подключение — в settings.py, в конец списка MIDDLEWARE:
    'core.readonly.ReadOnlyMiddleware',
Группу создать в админке: «Группы» → «Добавить» → название «Только просмотр»,
затем добавить в неё пользователя.
"""

from django.contrib import messages
from django.shortcuts import redirect

READ_ONLY_GROUP = 'Только просмотр'
ALLOWED_POST_PATHS = ('/login/', '/logout/', '/sites/select/')


def is_read_only(user):
    return user.is_authenticated and not user.is_superuser and user.groups.filter(name=READ_ONLY_GROUP).exists()


class ReadOnlyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (request.method == 'POST' and request.path not in ALLOWED_POST_PATHS
                and is_read_only(request.user)):
            messages.error(request, 'У вас доступ только на просмотр — изменения не сохранены.')
            return redirect(request.META.get('HTTP_REFERER') or '/')
        return self.get_response(request)
