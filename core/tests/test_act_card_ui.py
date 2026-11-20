"""Карточка акта: все поля остаются в форме, секции и замечания — на месте."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    ActMaterial, Certificate, ConstructionSite, Counterparty, DeliveryItem, DeliveryNote,
    DocumentationSection, HiddenWorkAct, Material, Standard,
)
from core.views import _material_catalog


class ActCardUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('tester', password='pass')
        customer = Counterparty.objects.create(short_name='Заказчик', inn='1')
        contractor = Counterparty.objects.create(short_name='Подрядчик', inn='2')
        designer = Counterparty.objects.create(short_name='Проектировщик', inn='3')
        supplier = Counterparty.objects.create(short_name='Поставщик', inn='4')
        cls.site = ConstructionSite.objects.create(
            name='Объект', address='Адрес', customer=customer,
            general_contractor=contractor, designer=designer,
        )
        standard = Standard.objects.create(name='СП', designation='СП 1')
        cls.section = DocumentationSection.objects.create(
            site=cls.site, name='Раздел', order_number=1, standard=standard,
        )
        cls.act = HiddenWorkAct.objects.create(
            act_number='1', drawn_up_date=date(2026, 11, 20), section=cls.section,
        )
        cls.material = Material.objects.create(name='Бетон B25', unit='м3', category='Бетон')
        Certificate.objects.create(
            document_type=Certificate.DocType.TEST_PROTOCOL,
            document_number='П-1', issue_date=date(2026, 1, 1),
        )
        cls.note = DeliveryNote.objects.create(
            number='ТН-1', delivery_date=date(2026, 11, 18), site=cls.site,
            supplier=supplier, has_quality_passport=False,
        )
        cls.item = DeliveryItem.objects.create(
            delivery_note=cls.note, material=cls.material,
            original_text='Бетон B25', quantity=Decimal('5'), unit='м3',
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_all_form_fields_are_rendered(self):
        page = self.client.get(reverse('act_edit', args=[self.act.pk]))

        for name in ('act_number', 'drawn_up_date', 'section', 'status',
                     'next_work_type', 'next_work_override', 'copies_count', 'protocols'):
            self.assertContains(page, f'name="{name}"')
        # Шапка акта: все 21 поле доступны для правки.
        for name in ('object_name', 'customer_inn', 'contractor_sro_code', 'designer_address',
                     'design_doc_reference'):
            self.assertContains(page, f'name="header_{name}"')

    def test_sections_navigation_and_warning_anchors(self):
        page = self.client.get(reverse('act_edit', args=[self.act.pk]))

        for panel in ('panel-req', 'panel-vol', 'panel-mat', 'panel-sig',
                      'panel-att', 'panel-docs', 'act-header'):
            self.assertContains(page, f'id="{panel}"')
        self.assertContains(page, 'class="act-nav"')
        self.assertContains(page, 'data-warning-section="panel-vol"')
        self.assertContains(page, 'data-warning-section="panel-mat"')

    def test_material_catalog_has_recent_and_search(self):
        ActMaterial.objects.create(act=self.act, delivery_item=self.item,
                                   written_off_volume=Decimal('1'))
        catalog = _material_catalog(self.act)

        self.assertEqual(len(catalog['recent']), 1)
        recent = catalog['recent'][0]
        self.assertEqual(recent['name'], 'Бетон B25')
        self.assertEqual(recent['note_number'], 'ТН-1')
        self.assertTrue(recent['in_act'])
        self.assertIn('бетон', recent['search'])

        flat = [m for g in catalog['groups'] for m in g['materials']]
        self.assertTrue(any(m['name'] == 'Бетон B25' and m['in_act'] for m in flat))
