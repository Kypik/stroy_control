from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    ActSigner, ConstructionSite, Counterparty, DocumentationSection,
    HiddenWorkAct, Person, Room, SiteSigner, Standard, WorkType, WorkVolume,
)


class ActFromVolumesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('tester', password='pass')
        cls.customer = Counterparty.objects.create(short_name='Заказчик', inn='1')
        cls.contractor = Counterparty.objects.create(short_name='Генподрядчик', inn='2')
        cls.designer = Counterparty.objects.create(short_name='Проектировщик', inn='3')
        cls.standard = Standard.objects.create(name='СП', designation='СП 1')

        cls.site_a = ConstructionSite.objects.create(
            name='Объект А', address='Адрес А', customer=cls.customer,
            general_contractor=cls.contractor, designer=cls.designer,
            design_doc_reference='Проект 1-2026',
        )
        cls.site_b = ConstructionSite.objects.create(
            name='Объект Б', address='Адрес Б', customer=cls.customer,
            general_contractor=cls.contractor, designer=cls.designer,
        )
        cls.section_a = DocumentationSection.objects.create(
            site=cls.site_a, name='Раздел А', standard=cls.standard, order_number=1)
        cls.section_b = DocumentationSection.objects.create(
            site=cls.site_b, name='Раздел Б', standard=cls.standard, order_number=1)
        cls.empty_section = DocumentationSection.objects.create(
            site=cls.site_a, name='Пустой раздел', standard=cls.standard, order_number=2)

        cls.work_a = WorkType.objects.create(
            name='Работа А', section=cls.section_a, requires_hidden_work_act=True,
            standard=cls.standard)
        cls.work_b = WorkType.objects.create(
            name='Работа Б', section=cls.section_b, requires_hidden_work_act=True,
            standard=cls.standard)
        WorkType.objects.create(
            name='Работа без замеров', section=cls.empty_section,
            requires_hidden_work_act=True, standard=cls.standard)

        cls.room_a = Room.objects.create(explication_name='Помещение А', site=cls.site_a)
        cls.room_b = Room.objects.create(explication_name='Помещение Б', site=cls.site_b)

        cls.existing_act = HiddenWorkAct.objects.create(
            act_number='3', drawn_up_date=date.today(), section=cls.section_a)
        cls.attached = WorkVolume.objects.create(
            work_type=cls.work_a, room=cls.room_a, actual_volume=5, unit='м2',
            act=cls.existing_act)
        cls.free1 = WorkVolume.objects.create(
            work_type=cls.work_a, room=cls.room_a, location='Оси 1-2',
            performance_date=date(2026, 1, 10), end_date=date(2026, 1, 12),
            actual_volume=10, unit='м2')
        cls.free2 = WorkVolume.objects.create(
            work_type=cls.work_a, room=cls.room_a, location='Оси 3-4',
            performance_date=date(2026, 1, 13), actual_volume=12, unit='м2')
        cls.foreign = WorkVolume.objects.create(
            work_type=cls.work_b, room=cls.room_b,
            actual_volume=7, unit='м')

        cls.person_a = Person.objects.create(
            organization=cls.customer, last_name='Представитель', first_name='Заказчика')
        SiteSigner.objects.create(
            site=cls.site_a, person=cls.person_a, role_in_act=ActSigner.Role.CUSTOMER)

    def setUp(self):
        self.client.force_login(self.user)

    def url(self, section=None):
        url = reverse('site_act_from_volumes', args=[self.site_a.pk])
        if section is not None:
            url += f'?section={section.pk}'
        return url

    def post(self, volumes=(), section='default', site_url=None):
        data = {'volume': [v.pk for v in volumes]}
        if section == 'default':
            section = self.section_a
        if section is not None:
            data['section'] = section.pk
        return self.client.post(site_url or self.url(), data)

    def acts(self):
        return HiddenWorkAct.objects.filter(section=self.section_a)

    # --- показ свободных замеров ---

    def test_get_shows_only_free_volumes_of_selected_section(self):
        response = self.client.get(self.url(self.section_a))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['free_volumes']), [self.free1, self.free2])
        self.assertContains(response, 'Оси 1-2')
        self.assertNotContains(response, 'Работа Б')

    def test_get_has_section_counters(self):
        response = self.client.get(self.url())
        sections = {s.pk: s for s in response.context['sections']}
        self.assertEqual(sections[self.section_a.pk].free_count, 2)
        self.assertEqual(sections[self.empty_section.pk].free_count, 0)

    def test_get_empty_section_shows_message(self):
        response = self.client.get(self.url(self.empty_section))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'нет свободных замеров')

    def test_foreign_section_is_404(self):
        response = self.client.get(
            reverse('site_act_from_volumes', args=[self.site_a.pk])
            + f'?section={self.section_b.pk}')
        self.assertEqual(response.status_code, 404)

    # --- создание акта ---

    def test_creates_act_and_attaches_volumes(self):
        before = self.acts().count()
        response = self.post([self.free1, self.free2])

        self.assertRedirects(
            response,
            reverse('act_edit', args=[self.acts().latest('pk').pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(self.acts().count(), before + 1)
        act = self.acts().latest('pk')
        self.assertEqual(act.act_number, '4')          # в разделе уже есть акт №3
        self.assertEqual(act.drawn_up_date, date.today())
        self.assertEqual(act.status, HiddenWorkAct.Status.DRAFT)
        self.assertEqual(act.section, self.section_a)

        self.free1.refresh_from_db()
        self.free2.refresh_from_db()
        self.assertEqual(self.free1.act, act)
        self.assertEqual(self.free2.act, act)

    def test_header_is_snapshotted_from_site(self):
        self.post([self.free1])
        act = self.acts().latest('pk')
        self.assertEqual(act.header_snapshot['object_name'], self.site_a.name)
        self.assertEqual(act.header_snapshot['object_address'], self.site_a.address)
        self.assertEqual(
            act.header_snapshot['design_doc_reference'], 'Проект 1-2026')

    def test_site_signers_are_copied(self):
        self.post([self.free1])
        act = self.acts().latest('pk')
        signers = list(ActSigner.objects.filter(act=act))
        self.assertEqual(len(signers), 1)
        self.assertEqual(signers[0].person, self.person_a)
        self.assertEqual(signers[0].role_in_act, ActSigner.Role.CUSTOMER)

    def test_number_is_separate_per_section(self):
        self.post([self.free1])
        self.assertEqual(self.acts().latest('pk').act_number, '4')

        other = HiddenWorkAct.objects.create(
            act_number='10', drawn_up_date=date.today(), section=self.section_b)
        self.post([self.free2])
        self.assertEqual(self.acts().latest('pk').act_number, '5')
        self.assertEqual(other.act_number, '10')

    # --- отказы ---

    def test_nothing_selected_shows_message_and_creates_nothing(self):
        before = self.acts().count()
        response = self.post([])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.acts().count(), before)
        self.assertContains(response, 'Отметьте хотя бы один замер')

    def test_no_section_shows_message(self):
        before = self.acts().count()
        response = self.post([self.free1], section=None)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.acts().count(), before)
        self.assertContains(response, 'Выберите раздел')

    def test_foreign_volume_cannot_be_attached(self):
        before = self.acts().count()
        response = self.post([self.foreign])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.acts().count(), before)
        self.foreign.refresh_from_db()
        self.assertIsNone(self.foreign.act)
        self.assertContains(response, 'другому разделу')

    def test_already_attached_volume_cannot_be_reused(self):
        before = self.acts().count()
        response = self.post([self.attached])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.acts().count(), before)
        self.attached.refresh_from_db()
        self.assertEqual(self.attached.act, self.existing_act)
        self.assertContains(response, 'уже привязана')

    def test_second_post_with_same_volume_is_rejected(self):
        self.post([self.free1])
        act = self.acts().latest('pk')
        before = self.acts().count()

        response = self.post([self.free1])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.acts().count(), before)
        self.free1.refresh_from_db()
        self.assertEqual(self.free1.act, act)

    def test_non_numeric_volume_id_is_rejected(self):
        before = self.acts().count()
        response = self.client.post(
            self.url(), {'section': self.section_a.pk, 'volume': ['не число']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.acts().count(), before)
        self.assertContains(response, 'Некорректный список замеров')

    def test_foreign_section_post_is_404(self):
        before = self.acts().count()
        response = self.post([self.free1], site_url=self.url())
        self.assertEqual(response.status_code, 302)     # контрольный успешный POST
        self.assertEqual(self.acts().count(), before + 1)

        response = self.post([self.free2], section=self.section_b, site_url=self.url())
        self.assertEqual(response.status_code, 404)

    # --- ссылки ---

    def test_links_to_screen_from_acts_and_volumes(self):
        url = reverse('site_act_from_volumes', args=[self.site_a.pk])
        response = self.client.get(reverse('site_acts', args=[self.site_a.pk]))
        self.assertContains(response, url)

        response = self.client.get(reverse('site_volumes', args=[self.site_a.pk]))
        self.assertContains(response, url)
