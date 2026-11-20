"""
core/security.py

Ограничения доступа для публикации через туннель.

Админка нужна только локально: запросы к /admin/ с внешнего хоста (туннеля)
получают 404, поэтому даже подобранный пароль не даёт доступа снаружи.
"""

from django.http import Http404

LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}


def _hostname(request):
    host = request.get_host().lower()
    if host.startswith('['):                # [::1]:8000
        return host[1:host.find(']')]
    return host.split(':')[0]               # localhost:8000


class AdminLocalOnlyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == '/admin' or request.path.startswith('/admin/'):
            forwarded = (
                request.META.get('HTTP_X_FORWARDED_HOST')
                or request.META.get('HTTP_X_FORWARDED_FOR')
                or request.META.get('HTTP_CF_CONNECTING_IP')
            )
            if _hostname(request) not in LOCAL_HOSTS or forwarded:
                raise Http404
        return self.get_response(request)
