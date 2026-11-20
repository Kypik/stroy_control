from datetime import date

from django.db import migrations


SITE_NAME = '«Культурно-досуговый центр с кинотеатром»'
CONTRACT_REFERENCE = '№ 07/05/2026-ЧОПК от 07.07.2026'
ACT_LOCATIONS = {
    '06/26/Ст-1': 'на отм. +6,200 в осях 9-16/Г-К, на отм. +10,400 в осях 9-16/Ж-Р',
    '07/26/Ст-2': 'на отм. +6,200 в осях 8-15/Л-Р',
    '06/26/Шт-1': 'на отм. +6,300 в кинотеатре К/З № 1 в осях 12-16/К-П',
    '06/26/Шт-3': 'на отм. +6,300 в кинотеатре К/З № 3 в осях 11-13/Г-И',
    '06/26/Шт-4': 'на отм. +6,300 в кинотеатре К/З № 4 в осях 10-12/Г-И',
    '06/26/Шт-5': 'на отм. +6,300 в кинотеатре К/З № 5 в осях 8-10/Г-И',
    '06/26/Шт-6': 'на отм. +6,300 в кинотеатре К/З № 6 в осях 9-12/И-Л',
    '06/26/Шт-7': 'на отм. +6,300 в кинотеатре К/З № 7 в осях 8-9/М-К',
    '06/26/Шт-8': 'на отм. +10,500 в кинотеатре К/З № 8 в осях 8-9/М-Р',
}
ROOM_NAMES = [f'Кинотеатр К/З № {number}' for number in (1, 3, 4, 5, 6, 7, 8)]


def has_references(room):
    for relation in room._meta.related_objects:
        accessor = relation.get_accessor_name()
        if not accessor:
            continue
        try:
            related = getattr(room, accessor)
        except relation.related_model.DoesNotExist:
            continue
        if hasattr(related, 'exists'):
            if related.exists():
                return True
        elif related is not None:
            return True
    return False


def fill_act_locations_and_dates(apps, schema_editor):
    ConstructionSite = apps.get_model('core', 'ConstructionSite')
    HiddenWorkAct = apps.get_model('core', 'HiddenWorkAct')
    Room = apps.get_model('core', 'Room')

    site = ConstructionSite.objects.filter(name=SITE_NAME).first()
    if site is None:
        return

    site.contract_reference = CONTRACT_REFERENCE
    site.save(update_fields=['contract_reference'])

    acts = HiddenWorkAct.objects.filter(section__site=site, act_number__in=ACT_LOCATIONS)
    for act in acts:
        end_date = date(2026, 7, 14) if act.act_number == '07/26/Ст-2' else date(2026, 6, 14)
        for volume in act.workvolume_set.all():
            volume.location = ACT_LOCATIONS[act.act_number]
            volume.end_date = end_date
            volume.room = None
            volume.save(update_fields=['location', 'end_date', 'room'])

    for room in Room.objects.filter(site=site, explication_name__in=ROOM_NAMES):
        if not has_references(room):
            room.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_actmaterial_material_and_more'),
    ]

    operations = [
        migrations.RunPython(fill_act_locations_and_dates, migrations.RunPython.noop),
    ]
