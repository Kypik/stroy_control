"""Обзор объекта и списки: быстрые действия, поиск и компактные фильтры."""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Certificate, ConstructionSite, Counterparty


class ListsUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('tester', password='pass')
        customer = Counterparty.objects.create(short_name='Заказчик', inn='1')
        contractor = Counterparty.objects.create(short_name='Подрядчик', inn='2')
        designer = Counterparty.objects.create(short_name='Проектировщик', inn='3')
        cls.site = ConstructionSite.objects.create(
            name='Жилой дом на Ленина', address='г. Москва, ул. Ленина, 1',
            customer=customer, general_contractor=contractor, designer=designer,
        )
        cls.other_site = ConstructionSite.objects.create(
            name='Школа', address='г. Москва, ул. Школьная, 2',
            customer=customer, general_contractor=contractor, designer=designer,
        )
        Certificate.objects.create(
            document_number='С-1', document_type=Certificate.DocType.CERTIFICATE,
            issue_date=date(2026, 1, 1), expiry_date=date(2027, 1, 1),
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_site_list_has_search(self):
        page = self.client.get(reverse('site_list'), {'q': 'Ленина'})
        self.assertContains(page, reverse('site_overview', args=[self.site.pk]))
        self.assertNotContains(page, reverse('site_overview', args=[self.other_site.pk]))

        empty = self.client.get(reverse('site_list'), {'q': 'нет такого'})
        self.assertContains(empty, 'ничего не найдено')

    def test_overview_has_quick_actions_and_compact_blocks(self):
        page = self.client.get(reverse('site_overview', args=[self.site.pk]))
        self.assertContains(page, 'Новый акт')
        self.assertContains(page, 'Акт из замеров')
        self.assertContains(page, 'Загрузить сертификаты')
        self.assertContains(page, 'Что требует работы')
        self.assertContains(page, 'Разделы объекта')
        self.assertContains(page, 'Реквизиты объекта')

    def test_reference_list_collapses_extra_filters(self):
        page = self.client.get(reverse('ref_list', args=['certificates']))
        self.assertContains(page, 'Ещё фильтры')
        self.assertContains(page, 'filters-more')
        # по умолчанию дополнительные фильтры закрыты
        self.assertNotContains(page, '<details class="filters-more" open>')

    def test_reference_list_opens_extra_filters_when_active(self):
        page = self.client.get(reverse('ref_list', args=['certificates']),
                               {'f_source': 'manual'})
        self.assertContains(page, '<details class="filters-more" open>')
