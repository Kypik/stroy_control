from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import (
    ActSigner, ConstructionSite, Counterparty, DeliveryItem, DeliveryNote,
    DocumentationSection, EstimateLine, HiddenWorkAct, Material, Person, Room,
    SiteSigner, Standard, WorkType, WorkVolume,
)
from core.site_context import SESSION_KEY


class SiteSpaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('tester', password='pass')
        cls.customer = Counterparty.objects.create(short_name='Заказчик', inn='1')
        cls.contractor = Counterparty.objects.create(short_name='Генподрядчик', inn='2')
        cls.designer = Counterparty.objects.create(short_name='Проектировщик', inn='3')
        cls.supplier_a = Counterparty.objects.create(short_name='Поставщик А', inn='4')
        cls.supplier_b = Counterparty.objects.create(short_name='Поставщик Б', inn='5')
        cls.standard = Standard.objects.create(name='СП', designation='СП 1')

        cls.site_a = ConstructionSite.objects.create(
            name='Объект А', address='Адрес А', customer=cls.customer,
            general_contractor=cls.contractor, designer=cls.designer,
        )
        cls.site_b = ConstructionSite.objects.create(
            name='Объект Б', address='Адрес Б', customer=cls.customer,
            general_contractor=cls.contractor, designer=cls.designer,
        )

        cls.section_a = DocumentationSection.objects.create(
            site=cls.site_a, name='Раздел А', standard=cls.standard, order_number=1)
        cls.section_b = DocumentationSection.objects.create(
            site=cls.site_b, name='Раздел Б', standard=cls.standard, order_number=1)
        cls.work_a = WorkType.objects.create(
            name='Работа А', section=cls.section_a, requires_hidden_work_act=True,
            standard=cls.standard)
        cls.work_b = WorkType.objects.create(
            name='Работа Б', section=cls.section_b, requires_hidden_work_act=True,
            standard=cls.standard)
        cls.room_a = Room.objects.create(explication_name='Помещение А', site=cls.site_a)
        cls.room_b = Room.objects.create(explication_name='Помещение Б', site=cls.site_b)

        cls.act_a = HiddenWorkAct.objects.create(
            act_number='АКТ-А', drawn_up_date=date.today(), section=cls.section_a)
        cls.act_b = HiddenWorkAct.objects.create(
            act_number='АКТ-Б', drawn_up_date=date.today(), section=cls.section_b)

        cls.note_a = DeliveryNote.objects.create(
            number='НАКЛ-А', delivery_date=date.today(), site=cls.site_a,
            supplier=cls.supplier_a, has_quality_passport=False)
        cls.note_b = DeliveryNote.objects.create(
            number='НАКЛ-Б', delivery_date=date.today(), site=cls.site_b,
            supplier=cls.supplier_b, has_quality_passport=False)
        cls.item_a = DeliveryItem.objects.create(
            delivery_note=cls.note_a, original_text='Строка А', quantity=1)
        cls.item_b = DeliveryItem.objects.create(
            delivery_note=cls.note_b, original_text='Строка Б', quantity=1)

        cls.volume_a = WorkVolume.objects.create(
            work_type=cls.work_a, room=cls.room_a, actual_volume=10, unit='м2')
        cls.volume_b = WorkVolume.objects.create(
            work_type=cls.work_b, room=cls.room_b, actual_volume=10, unit='м2')
        cls.estimate_a = EstimateLine.objects.create(
            room=cls.room_a, work_type=cls.work_a, estimated_volume=10)
        cls.estimate_b = EstimateLine.objects.create(
            room=cls.room_b, work_type=cls.work_b, estimated_volume=10)

        cls.person_a = Person.objects.create(
            organization=cls.customer, last_name='ТолькоА', first_name='Иван',
            authority_end_date=date.today() - timedelta(days=1))
        cls.person_b = Person.objects.create(
            organization=cls.customer, last_name='ТолькоБ', first_name='Пётр',
            authority_end_date=date.today() - timedelta(days=1))
        SiteSigner.objects.create(
            site=cls.site_a, person=cls.person_a, role_in_act=ActSigner.Role.CUSTOMER)
        SiteSigner.objects.create(
            site=cls.site_b, person=cls.person_b, role_in_act=ActSigner.Role.CUSTOMER)

        cls.material = Material.objects.create(name='Материал')

    def setUp(self):
        self.client.force_login(self.user)

    def select_site(self, site):
        session = self.client.session
        session[SESSION_KEY] = site.pk
        session.save()

    def test_site_acts_show_only_own_object(self):
        response = self.client.get(reverse('site_acts', args=[self.site_a.pk]))
        self.assertContains(response, self.act_a.act_number)
        self.assertNotContains(response, self.act_b.act_number)

    def test_site_deliveries_show_only_own_object(self):
        response = self.client.get(reverse('site_deliveries', args=[self.site_a.pk]))
        self.assertContains(response, self.note_a.number)
        self.assertNotContains(response, self.note_b.number)

    def test_site_journal_show_only_own_object(self):
        response = self.client.get(reverse('site_journal', args=[self.site_a.pk]))
        notes = {row['note'] for row in response.context['rows']}
        self.assertEqual(notes, {self.note_a.number})

    def test_site_volumes_show_only_own_object(self):
        response = self.client.get(reverse('site_volumes', args=[self.site_a.pk]))
        self.assertTrue(response.context['rows'])
        self.assertTrue(all(
            row['line'].room.site_id == self.site_a.pk for row in response.context['rows']
        ))

    def test_site_attention_show_only_own_object(self):
        response = self.client.get(reverse('site_attention', args=[self.site_a.pk]))
        self.assertContains(response, 'ТолькоА')
        self.assertNotContains(response, 'ТолькоБ')
        self.assertContains(response, 'Строка А')
        self.assertNotContains(response, 'Строка Б')

    def test_site_list_renders(self):
        response = self.client.get(reverse('site_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.site_a.name)
        self.assertContains(response, self.site_b.name)
        sites = {site.pk: site for site in response.context['sites']}
        self.assertEqual(sites[self.site_a.pk].acts_count, 1)
        self.assertEqual(sites[self.site_a.pk].deliveries_count, 1)

    def test_overview_counters(self):
        response = self.client.get(reverse('site_overview', args=[self.site_a.pk]))
        self.assertContains(response, self.site_a.name)
        self.assertContains(response, self.site_a.address)
        self.assertEqual(response.context['acts_total'], 1)
        self.assertEqual(response.context['free_volumes'], 1)
        self.assertEqual(response.context['unmatched_items'], 1)

    def test_root_uses_selected_site_or_site_list(self):
        self.select_site(self.site_a)
        self.assertRedirects(
            self.client.get(reverse('home')),
            reverse('site_overview', args=[self.site_a.pk]),
            fetch_redirect_response=False,
        )

        session = self.client.session
        session.pop(SESSION_KEY, None)
        session.save()
        self.assertRedirects(
            self.client.get(reverse('home')),
            reverse('site_list'),
            fetch_redirect_response=False,
        )

    def test_old_urls_redirect_to_site_equivalent(self):
        self.select_site(self.site_a)
        pairs = [
            ('act_list', 'site_acts'),
            ('act_create', 'site_act_create'),
            ('attention', 'site_attention'),
            ('delivery_matching', 'site_delivery_matching'),
            ('journal', 'site_journal'),
            ('volumes_compare', 'site_volumes'),
        ]
        for old_name, new_name in pairs:
            with self.subTest(old_name=old_name):
                self.assertRedirects(
                    self.client.get(reverse(old_name)),
                    reverse(new_name, args=[self.site_a.pk]),
                    fetch_redirect_response=False,
                )

    def test_old_deliveries_registry_redirects_to_site_section(self):
        self.select_site(self.site_a)
        response = self.client.get(reverse('ref_list', args=['deliveries']), {'q': 'Накл'})
        self.assertRedirects(
            response,
            reverse('site_deliveries', args=[self.site_a.pk]) + '?q=%D0%9D%D0%B0%D0%BA%D0%BB',
            fetch_redirect_response=False,
        )

    def test_old_url_keeps_query_parameters(self):
        self.select_site(self.site_a)
        response = self.client.get(reverse('act_list'), {'status': 'DRAFT'})
        self.assertRedirects(
            response,
            reverse('site_acts', args=[self.site_a.pk]) + '?status=DRAFT',
            fetch_redirect_response=False,
        )

    def test_old_journal_honours_explicit_site(self):
        self.select_site(self.site_a)
        response = self.client.get(reverse('journal'), {'site': self.site_b.pk, 'from': '01.01.2026'})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('site_journal', args=[self.site_b.pk]), response['Location'])
        self.assertIn('from=01.01.2026', response['Location'])

    def test_site_edit_is_separate_from_overview(self):
        self.assertEqual(
            reverse('site_edit', args=[self.site_a.pk]),
            f'/sites/{self.site_a.pk}/edit/',
        )
        self.assertEqual(
            self.client.get(reverse('site_overview', args=[self.site_a.pk])).status_code,
            200,
        )
        self.assertEqual(
            self.client.get(reverse('site_edit', args=[self.site_a.pk])).status_code,
            200,
        )

    def test_overview_quick_action_from_volumes_renders(self):
        response = self.client.get(reverse('site_act_from_volumes', args=[self.site_a.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Создать акт из замеров')

    def test_global_attention_all_renders(self):
        response = self.client.get(reverse('attention_all'))
        self.assertEqual(response.status_code, 200)

    def test_act_create_sections_are_scoped(self):
        self.select_site(self.site_a)
        response = self.client.get(reverse('site_act_create', args=[self.site_a.pk]))
        sections = list(response.context['form'].fields['section'].queryset)
        self.assertEqual(sections, [self.section_a])

    def test_act_edit_dropdowns_are_scoped(self):
        self.select_site(self.site_a)
        response = self.client.get(reverse('act_edit', args=[self.act_a.pk]))
        volumes = response.context['volumes']
        materials = response.context['materials']
        self.assertEqual(
            list(volumes.empty_form.fields['work_type'].queryset), [self.work_a])
        self.assertEqual(
            list(materials.empty_form.fields['delivery_item'].queryset), [self.item_a])

    def test_ref_forms_are_scoped(self):
        self.select_site(self.site_a)
        cases = [
            ('worktypes', 'section', [self.section_a]),
            ('rooms', 'site', [self.site_a]),
            ('deliveries', 'site', [self.site_a]),
            ('volumes', 'work_type', [self.work_a]),
            ('volumes', 'room', [self.room_a]),
            ('volumes', 'act', [self.act_a]),
            ('estimate', 'room', [self.room_a]),
            ('estimate', 'work_type', [self.work_a]),
            ('inspections', 'delivery_item', [self.item_a]),
        ]
        for slug, field_name, expected in cases:
            with self.subTest(slug=slug, field=field_name):
                response = self.client.get(reverse('ref_create', args=[slug]))
                actual = list(response.context['form'].fields[field_name].queryset)
                self.assertEqual(actual, expected)

    def test_ref_list_is_scoped_by_selected_site(self):
        self.select_site(self.site_a)
        response = self.client.get(reverse('ref_list', args=['sections']))
        self.assertEqual(response.context['page'].paginator.count, 1)
        self.assertContains(response, self.section_a.name)
        self.assertNotContains(response, self.section_b.name)

    def test_matching_post_cannot_touch_foreign_item(self):
        response = self.client.post(
            reverse('site_delivery_matching', args=[self.site_a.pk]),
            {'action': 'save', f'item_{self.item_b.pk}': self.material.pk},
        )
        self.assertEqual(response.status_code, 302)
        self.item_b.refresh_from_db()
        self.assertIsNone(self.item_b.material_id)

    def test_matching_cannot_paste_into_foreign_note(self):
        response = self.client.post(
            reverse('site_delivery_matching', args=[self.site_a.pk]),
            {'action': 'paste', 'note': self.note_b.pk, 'lines': 'Новая строка; 2; шт'},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(DeliveryItem.objects.filter(delivery_note=self.note_b).count(), 1)

    def test_switcher_saves_site_to_session(self):
        response = self.client.post(reverse('site_select'), {'site': self.site_b.pk})
        self.assertRedirects(
            response, reverse('site_overview', args=[self.site_b.pk]),
            fetch_redirect_response=False,
        )
        self.assertEqual(self.client.session[SESSION_KEY], self.site_b.pk)
