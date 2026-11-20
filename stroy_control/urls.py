from django.contrib import admin
from django.urls import include, path

# Медиафайлы наружу не раздаются: все скачивания идут через вьюхи с @login_required.
urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
]