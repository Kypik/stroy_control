"""Статус акта — управляемый workflow, а не свободный select."""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.act_workflow import is_allowed, transition_buttons
from core.models import (
    ConstructionSite, Counterparty, DocumentationSection, EventLog, HiddenWorkAct, Standard,
)


class ActWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('tester', password='pass')
        customer = Counterparty.objects.create(short_name='Заказчик', inn='1')
        contractor = Counterparty.objects.create(short_name='Подрядчик', inn='2')
        designer = Counterparty.objects.create(short_name='Проектировщик', inn='3')
        cls.site = ConstructionSite.objects.create(
            name='Объект', address='Адрес', customer=customer,
            general_contractor=contractor, designer=designer,
        )
        standard = Standard.objects.create(name='СП', designation='СП 1')
        cls.section = DocumentationSection.objects.create(
            site=cls.site, name='Раздел', order_number=1, standard=standard,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _act(self, status=HiddenWorkAct.Status.DRAFT):
        return HiddenWorkAct.objects.create(
            act_number='1', drawn_up_date=date(2026, 11, 20),
            section=self.section, status=status,
        )

    def _post(self, act, **extra):
        data = {
            'act_number': act.act_number,
            'drawn_up_date': act.drawn_up_date.strftime('%d.%m.%Y'),
            'section': act.section_id,
            'status': act.status,
            'copies_count': act.copies_count or 4,
            'next_work_override': act.next_work_override,
        }
        for prefix in ('vol', 'mat', 'sig', 'att'):
            data[f'{prefix}-TOTAL_FORMS'] = '0'
            data[f'{prefix}-INITIAL_FORMS'] = '0'
        data.update(extra)
        return data

    def test_transition_matrix(self):
        self.assertTrue(is_allowed(HiddenWorkAct.Status.DRAFT, HiddenWorkAct.Status.PENDING))
        self.assertFalse(is_allowed(HiddenWorkAct.Status.DRAFT, HiddenWorkAct.Status.ACCEPTED))
        self.assertTrue(is_allowed(HiddenWorkAct.Status.PRINTING, HiddenWorkAct.Status.ACCEPTED))
        self.assertEqual(
            [b['value'] for b in transition_buttons(self._act())],
            [HiddenWorkAct.Status.PENDING],
        )

    def test_page_shows_workflow_button_and_hides_status_select(self):
        act = self._act()
        page = self.client.get(reverse('act_edit', args=[act.pk]))

        self.assertContains(page, 'Отправить на подписание')
        self.assertContains(page, 'name="status"')
        self.assertNotContains(page, '<select name="status"')

    def test_allowed_transition_saves_and_logs(self):
        act = self._act()
        response = self.client.post(
            reverse('act_edit', args=[act.pk]),
            self._post(act, transition=HiddenWorkAct.Status.PENDING),
        )
        self.assertEqual(response.status_code, 302)

        act.refresh_from_db()
        self.assertEqual(act.status, HiddenWorkAct.Status.PENDING)
        self.assertTrue(EventLog.objects.filter(action='Изменён статус акта').exists())

    def test_forbidden_transition_does_not_save(self):
        act = self._act()
        response = self.client.post(
            reverse('act_edit', args=[act.pk]),
            self._post(act, transition=HiddenWorkAct.Status.ACCEPTED),
        )

        self.assertEqual(response.status_code, 200)
        act.refresh_from_db()
        self.assertEqual(act.status, HiddenWorkAct.Status.DRAFT)
        self.assertContains(response, 'переход статуса недоступен')

    def test_hidden_status_cannot_be_tampered(self):
        act = self._act()
        response = self.client.post(
            reverse('act_edit', args=[act.pk]),
            self._post(act, status=HiddenWorkAct.Status.ACCEPTED),
        )

        self.assertEqual(response.status_code, 302)
        act.refresh_from_db()
        self.assertEqual(act.status, HiddenWorkAct.Status.DRAFT)

    def test_save_without_transition_keeps_status(self):
        act = self._act(HiddenWorkAct.Status.PRINTING)
        response = self.client.post(reverse('act_edit', args=[act.pk]), self._post(act))

        self.assertEqual(response.status_code, 302)
        act.refresh_from_db()
        self.assertEqual(act.status, HiddenWorkAct.Status.PRINTING)
