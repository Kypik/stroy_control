"""Что видно в пространстве объекта из глобальных справочников материалов и сертификатов.

Копий записей нет: это обычные фильтры по связям моделей.
Сертификат по-прежнему относится к материалу, а не к объекту; объект лишь
определяет, какие сертификаты показывать.
"""

from django.db.models import Count, Max, Q

from .models import Certificate, Material


def certificates_for_site(site):
    """Сертификаты объекта: материалы, поставлявшиеся на объект,
    плюс документы из загрузок, сделанных в этом объекте."""
    return Certificate.objects.filter(
        Q(materialcertificate__material__deliveryitem__delivery_note__site=site)
        | Q(document__certificateupload__site=site)
    ).distinct()


def materials_for_site(site):
    """Материалы, встречающиеся в накладных объекта. Справочник материалов остаётся общим."""
    return Material.objects.filter(
        deliveryitem__delivery_note__site=site,
    ).annotate(
        deliveries_count=Count('deliveryitem', distinct=True),
        last_delivery=Max('deliveryitem__delivery_note__delivery_date'),
    ).order_by('name')
