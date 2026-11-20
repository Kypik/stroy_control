"""Шапка акта — снимок, а не живая карточка объекта.

Проверяем: снимок создаётся при создании акта; правка карточки объекта не меняет
старые акты; скрытый блок меняет только один акт; Excel/предпросмотр/реестр
используют снимок; копия акта сохраняет снимок.
"""

import os
import tempfile
from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    ConstructionSite, Counterparty, DocumentationSection, HiddenWorkAct, SRO, Standard,
)
from core.services import (
    build_header_context, header_for_act, header_rows, header_to_snapshot,
)


class ActHeaderSnapshotTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('tester', password='pass')
        cls.customer = Counterparty.objects.create(
            short_name='Заказчик', inn='111', ogrn='222', address='Адрес заказчика')
        cls.contractor = Counterparty.objects.create(
            short_name='Подрядчик', inn='333', ogrn='444', address='Адрес подрядчика')
        cls.designer = Counterparty.objects.create(
            short_name='Проектировщик', inn='555', ogrn='666', address='Адрес проектировщика')
        SRO.objects.create(organization=cls.contractor, name='СРО подрядчиков',
                           code='СРО-1', issue_date=date(2020, 1, 10))
        SRO.objects.create(organization=cls.designer, name='СРО проектировщиков',
                           code='СРО-2', issue_date=date(2020, 2, 20))

        cls.site = ConstructionSite.objects.create(
            name='Объект А', address='Адрес объекта', customer=cls.customer,
            general_contractor=cls.contractor, designer=cls.designer,
            design_doc_reference='Проект 12-2020',
        )
        cls.standard = Standard.objects.create(name='СП', designation='СП 1')
        cls.section = DocumentationSection.objects.create(
            site=cls.site, name='Раздел', standard=cls.standard, order_number=1)

    def setUp(self):
        self.client.force_login(self.user)

    # -- вспомогательное -------------------------------------------------
    def _make_act(self, number):
        return HiddenWorkAct.objects.create(
            act_number=number, drawn_up_date=date(2026, 11, 20), section=self.section,
            header_snapshot=header_to_snapshot(build_header_context(self.site.pk)),
        )

    def _act_post(self, act, overrides=None, **extra):
        header = header_for_act(act)
        data = {
            'act_number': act.act_number,
            'drawn_up_date': act.drawn_up_date.strftime('%d.%m.%Y'),
            'section': act.section_id,
            'status': act.status,
            'copies_count': act.copies_count or 4,
            'next_work_override': act.next_work_override,
        }
        overrides = overrides or {}
        for row in header_rows(header):
            data[f'header_{row["name"]}'] = overrides.get(row['name'], row['value'])
        for prefix in ('vol', 'mat', 'sig', 'att'):
            data[f'{prefix}-TOTAL_FORMS'] = '0'
            data[f'{prefix}-INITIAL_FORMS'] = '0'
        data.update(extra)
        return data

    # -- тесты ------------------------------------------------------------
    def test_act_creation_saves_site_header(self):
        data = {
            'section': self.section.pk,
            'drawn_up_date': '20.11.2026',
            'status': HiddenWorkAct.Status.DRAFT,
            'act_number': '',
            'copies_count': '4',
        }
        for row in header_rows(build_header_context(self.site.pk)):
            data[f'header_{row["name"]}'] = row['value']
        response = self.client.post(reverse('site_act_create', args=[self.site.pk]), data)
        self.assertEqual(response.status_code, 302)

        act = HiddenWorkAct.objects.get(act_number='1')
        self.assertEqual(act.header_snapshot['object_name'], 'Объект А')
        self.assertEqual(act.header_snapshot['customer_inn'], '111')
        self.assertEqual(act.header_snapshot['contractor_sro_code'], 'СРО-1')
        self.assertEqual(act.header_snapshot['contractor_sro_date'], '2020-01-10')

    def test_changing_site_card_does_not_change_old_act_header(self):
        act = self._make_act('1')
        self.customer.short_name = 'Новый заказчик'
        self.customer.inn = '999'
        self.customer.save()

        self.assertEqual(header_for_act(act)['customer_name'], 'Заказчик')
        self.assertEqual(header_for_act(act)['customer_inn'], '111')
        self.assertEqual(build_header_context(self.site.pk)['customer_name'], 'Новый заказчик')

    def test_hidden_block_changes_only_one_act(self):
        act_one = self._make_act('1')
        act_two = self._make_act('2')

        response = self.client.post(
            reverse('act_edit', args=[act_one.pk]),
            self._act_post(act_one, overrides={'customer_name': 'Особый заказчик'}),
        )
        self.assertEqual(response.status_code, 302)

        act_one.refresh_from_db()
        act_two.refresh_from_db()
        self.customer.refresh_from_db()
        self.assertEqual(act_one.header_snapshot['customer_name'], 'Особый заказчик')
        self.assertEqual(act_two.header_snapshot['customer_name'], 'Заказчик')
        self.assertEqual(self.customer.short_name, 'Заказчик')

    def test_reset_header_rebuilds_from_site(self):
        act = self._make_act('1')
        act.header_snapshot['customer_name'] = 'Особый заказчик'
        act.save(update_fields=['header_snapshot'])

        self.customer.short_name = 'Обновлённый заказчик'
        self.customer.save()

        response = self.client.post(
            reverse('act_edit', args=[act.pk]),
            self._act_post(act, overrides={'customer_name': 'Особый заказчик'},
                           header_action='reset'),
        )
        self.assertEqual(response.status_code, 302)
        act.refresh_from_db()
        self.assertEqual(act.header_snapshot['customer_name'], 'Обновлённый заказчик')

    def test_copy_keeps_header_snapshot(self):
        act = self._make_act('1')
        act.header_snapshot['customer_name'] = 'Особый заказчик'
        act.save(update_fields=['header_snapshot'])

        response = self.client.post(reverse('act_copy', args=[act.pk]))
        self.assertEqual(response.status_code, 302)
        copy = HiddenWorkAct.objects.exclude(pk=act.pk).get()
        self.assertEqual(copy.header_snapshot['customer_name'], 'Особый заказчик')

    def test_excel_uses_snapshot_not_live_site(self):
        from core import act_export

        act = self._make_act('1')
        self.customer.short_name = 'Новый заказчик'
        self.customer.save()
        self.site.name = 'Новый объект'
        self.site.save()

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(act_export, 'OUTPUT_DIR', tmp):
                path = act_export.generate_act_excel(act.pk)
                self.assertTrue(os.path.isfile(path))

                import openpyxl
                wb = openpyxl.load_workbook(path)
                ws = wb['1']
                self.assertEqual(ws.cell(row=2, column=10).value, 'Объект А')
                self.assertIn('Заказчик', str(ws.cell(row=6, column=2).value))
                self.assertNotIn('Новый заказчик', str(ws.cell(row=6, column=2).value))
                wb.close()

    def test_preview_uses_snapshot(self):
        from core.preview import PreviewUnavailable

        act = self._make_act('1')
        self.customer.short_name = 'Новый заказчик'
        self.customer.save()

        with patch('core.preview.build_preview_pages',
                   side_effect=PreviewUnavailable('нет LibreOffice')):
            response = self.client.get(reverse('act_preview', args=[act.pk]))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('ОГРН 222', html)
        self.assertNotIn('Новый заказчик', html)

    def test_registry_uses_snapshot(self):
        from pypdf import PdfReader

        from core.registry_export import generate_registry_pdf

        act = self._make_act('1')
        self.contractor.short_name = 'Новый подрядчик'
        self.contractor.save()

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'registry.pdf')
            generate_registry_pdf(act, path)
            reader = PdfReader(path)
            text = '\n'.join(page.extract_text() or '' for page in reader.pages)
            reader.stream.close()

        self.assertIn('Подрядчик', text)
        self.assertNotIn('Новый подрядчик', text)
