"""Группировка длинных форм: поля не теряются, обязательные видны сразу."""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.form_layouts import grouped_fields
from core.forms import make_form
from core.models import (
    Certificate, ConstructionSite, Counterparty, DeliveryNote, Person,
)


class FormLayoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('tester', password='pass')
        cls.customer = Counterparty.objects.create(short_name='Заказчик', inn='1')
        cls.contractor = Counterparty.objects.create(short_name='Подрядчик', inn='2')
        cls.designer = Counterparty.objects.create(short_name='Проектировщик', inn='3')
        cls.site = ConstructionSite.objects.create(
            name='Объект', address='Адрес', customer=cls.customer,
            general_contractor=cls.contractor, designer=cls.designer,
        )
        cls.certificate = Certificate.objects.create(
            document_number='С-1', document_type=Certificate.DocType.CERTIFICATE,
            issue_date=date(2026, 1, 1), expiry_date=date(2027, 1, 1),
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _assert_no_fields_lost(self, form, slug):
        groups = grouped_fields(form, slug)
        self.assertIsNotNone(groups)
        grouped = [field.name for group in groups for field in group['fields']]
        expected = [field.name for field in form.visible_fields()]
        self.assertEqual(sorted(grouped), sorted(expected))
        self.assertEqual(len(grouped), len(set(grouped)))

    def test_certificate_fields_are_grouped_without_loss(self):
        form = make_form(Certificate)(instance=self.certificate)
        self._assert_no_fields_lost(form, 'certificates')

        groups = grouped_fields(form, 'certificates')
        opened = {field.name for group in groups if group['open'] for field in group['fields']}
        for required in ('document_type', 'document_number', 'issue_date',
                         'verification_status', 'source'):
            self.assertIn(required, opened)

    def test_person_and_delivery_fields_are_grouped(self):
        person = Person.objects.create(
            organization=self.customer, last_name='Иванов', first_name='Иван',
        )
        note = DeliveryNote.objects.create(
            number='ТН-1', delivery_date=date(2026, 1, 1), site=self.site,
            supplier=self.contractor, has_quality_passport=False,
        )
        self._assert_no_fields_lost(make_form(Person)(instance=person), 'persons')
        self._assert_no_fields_lost(make_form(DeliveryNote)(instance=note), 'deliveries')

    def test_certificate_form_renders_groups(self):
        page = self.client.get(
            reverse('ref_edit', args=['certificates', self.certificate.pk]))
        self.assertContains(page, 'Документ')
        self.assertContains(page, 'Продукция и скан')
        self.assertContains(page, 'Данные реестра')
        self.assertContains(page, 'name="document_number"')
        self.assertContains(page, 'name="registry_url"')
        # FK получают поиск и признак модели — чтобы новая запись из «+ создать»
        # сразу появилась в нужном списке.
        self.assertContains(page, 'data-autocomplete="fk"')
        self.assertContains(page, 'data-model="counterparty"')

    def test_site_edit_renders_groups(self):
        page = self.client.get(reverse('site_edit', args=[self.site.pk]))
        self.assertContains(page, 'Документация и сроки')
        self.assertContains(page, 'Реестр к АОСР')
        self.assertContains(page, 'name="name"')
