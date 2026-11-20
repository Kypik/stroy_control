"""Проверки поведения акта: Excel формируется всегда, проблемы с документами
о качестве — предупреждения и «⚠» в файле, а не запрет на выпуск."""

import io
import shutil
import tempfile
from datetime import date
from decimal import Decimal

import openpyxl
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core import act_export, checks
from core.models import (
    ActMaterial, ConstructionSite, Counterparty, DeliveryItem, DeliveryNote,
    DocumentationSection, HiddenWorkAct, Material, Standard,
)
from core.views import act_warnings


class ActExcelWarningsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.customer = Counterparty.objects.create(short_name='Заказчик', inn='1')
        cls.contractor = Counterparty.objects.create(short_name='Генподрядчик', inn='2')
        cls.designer = Counterparty.objects.create(short_name='Проектировщик', inn='3')
        cls.supplier = Counterparty.objects.create(short_name='Поставщик', inn='4')
        standard = Standard.objects.create(name='СП', designation='СП 48.13330.2019')

        cls.site = ConstructionSite.objects.create(
            name='Объект', address='г. Москва, ул. Строителей, 1',
            customer=cls.customer, general_contractor=cls.contractor, designer=cls.designer,
        )
        cls.section = DocumentationSection.objects.create(
            site=cls.site, name='Раздел 1', standard=standard,
        )
        cls.material = Material.objects.create(name='Бетон B25', unit='м3')

        cls.act = HiddenWorkAct.objects.create(
            act_number='1', drawn_up_date=date(2026, 5, 1), section=cls.section,
        )
        ActMaterial.objects.create(
            act=cls.act, material=cls.material, written_off_volume=Decimal('1.5'),
        )

        cls.user = get_user_model().objects.create_user('tester', password='pass')

    def setUp(self):
        self.client.force_login(self.user)
        # Сгенерированные файлы — во временную папку, а не в media/ проекта.
        self.tmpdir = tempfile.mkdtemp()
        self._old_output_dir = act_export.OUTPUT_DIR
        act_export.OUTPUT_DIR = self.tmpdir
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.addCleanup(setattr, act_export, 'OUTPUT_DIR', self._old_output_dir)

    def test_excel_downloads_without_certificate(self):
        response = self.client.get(reverse('act_excel', args=[self.act.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('.xlsx', response['Content-Disposition'])

        content = b''.join(response.streaming_content)
        workbook = openpyxl.load_workbook(io.BytesIO(content))
        cells = [
            str(value)
            for row in workbook.active.iter_rows(values_only=True)
            for value in row if value is not None
        ]
        self.assertTrue(
            any('⚠' in cell and 'СЕРТИФИКАТ' in cell.upper() for cell in cells),
            'В файле нет отметки «⚠» об отсутствии сертификата',
        )

    def test_warnings_are_shown_after_download(self):
        self.client.get(reverse('act_excel', args=[self.act.pk]))
        page = self.client.get(reverse('act_edit', args=[self.act.pk]))

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Excel сформирован, но есть замечания')
        self.assertContains(page, 'нет сертификата')

    def test_certificate_problems_appear_in_act_card(self):
        warnings = act_warnings(self.act)

        self.assertTrue(any('нет сертификата' in w.lower() for w in warnings))
        self.assertTrue(any('⚠' in w for w in warnings))

        page = self.client.get(reverse('act_edit', args=[self.act.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'нет сертификата')

    def test_certificate_problems_are_warning_in_attention(self):
        groups = {g['title']: g for g in checks.attention()}

        group = groups['Акты: проблемы с документами о качестве']
        self.assertEqual(group['level'], 'warn')
        self.assertTrue(any('нет сертификата' in text.lower() for text, _ in group['items']))

    def test_data_errors_do_not_break_page(self):
        note = DeliveryNote.objects.create(
            number='ТН-1', delivery_date=date(2026, 5, 2), site=self.site,
            supplier=self.supplier, has_quality_passport=False,
        )
        item = DeliveryItem.objects.create(
            delivery_note=note, original_text='Бетон, как написано в накладной',
            quantity=Decimal('2'), unit='м3',
        )
        act = HiddenWorkAct.objects.create(
            act_number='2', drawn_up_date=date(2026, 5, 3), section=self.section,
        )
        ActMaterial.objects.create(act=act, delivery_item=item, written_off_volume=Decimal('1'))

        page = self.client.get(reverse('act_edit', args=[act.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'не сопоставлена')

        # Ошибка в данных не мешает скачать файл — в нём будет «⚠».
        response = self.client.get(reverse('act_excel', args=[act.pk]))
        self.assertEqual(response.status_code, 200)
