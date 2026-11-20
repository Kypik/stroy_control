import tempfile
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import (
    ActMaterial, Certificate, CertificateUpload, ConstructionSite, Counterparty,
    DeliveryItem, DeliveryNote, DocumentationSection, Documents, HiddenWorkAct,
    Material, MaterialCertificate, Standard,
)
from core.services import get_materials_for_act


class SiteCertificatesTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.TemporaryDirectory()
        cls._media_override = override_settings(MEDIA_ROOT=cls._media.name)
        cls._media_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._media_override.disable()
        cls._media.cleanup()

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('tester', password='pass')
        cls.customer = Counterparty.objects.create(short_name='Заказчик', inn='1')
        cls.contractor = Counterparty.objects.create(short_name='Генподрядчик', inn='2')
        cls.designer = Counterparty.objects.create(short_name='Проектировщик', inn='3')
        cls.supplier = Counterparty.objects.create(short_name='Поставщик', inn='4')
        cls.standard = Standard.objects.create(name='СП', designation='СП 1')

        cls.site_a = ConstructionSite.objects.create(
            name='Объект А', address='Адрес А', customer=cls.customer,
            general_contractor=cls.contractor, designer=cls.designer)
        cls.site_b = ConstructionSite.objects.create(
            name='Объект Б', address='Адрес Б', customer=cls.customer,
            general_contractor=cls.contractor, designer=cls.designer)

        cls.section_a = DocumentationSection.objects.create(
            site=cls.site_a, name='Раздел А', standard=cls.standard)
        cls.section_b = DocumentationSection.objects.create(
            site=cls.site_b, name='Раздел Б', standard=cls.standard)
        cls.act_b = HiddenWorkAct.objects.create(
            act_number='АКТ-Б', drawn_up_date=date.today(), section=cls.section_b)

        cls.material = Material.objects.create(name='Материал поставки', unit='шт')
        cls.note_a = DeliveryNote.objects.create(
            number='НАКЛ-А', delivery_date=date.today(), site=cls.site_a,
            supplier=cls.supplier, has_quality_passport=False)
        DeliveryItem.objects.create(
            delivery_note=cls.note_a, material=cls.material,
            original_text='Материал поставки', quantity=10)

        # Сертификат материала, поставленного только на объект А.
        cls.cert_delivered = Certificate.objects.create(
            document_number='СЕРТ-А', document_type=Certificate.DocType.CERTIFICATE,
            issue_date=date.today() - timedelta(days=30),
            expiry_date=date.today() + timedelta(days=30),
            verification_status=Certificate.Status.CONFIRMED)
        MaterialCertificate.objects.create(material=cls.material, certificate=cls.cert_delivered)

        # Сертификат, загруженный в объекте Б (без привязки к материалу).
        doc_b = Documents.objects.create(
            path_to_file='documents/cert_b.pdf', document_type='Документ о качестве (скан)')
        CertificateUpload.objects.create(document=doc_b, site=cls.site_b)
        cls.cert_uploaded_b = Certificate.objects.create(
            document_number='СЕРТ-Б', document_type=Certificate.DocType.CERTIFICATE,
            issue_date=date.today() - timedelta(days=10),
            expiry_date=date.today() + timedelta(days=10),
            document=doc_b, verification_status=Certificate.Status.CONFIRMED)

    def setUp(self):
        self.client.force_login(self.user)

    def test_delivered_material_certificate_visible_only_on_its_site(self):
        response_a = self.client.get(reverse('site_certificates', args=[self.site_a.pk]))
        self.assertContains(response_a, 'СЕРТ-А')
        self.assertNotContains(response_a, 'СЕРТ-Б')

        response_b = self.client.get(reverse('site_certificates', args=[self.site_b.pk]))
        self.assertNotContains(response_b, 'СЕРТ-А')
        self.assertContains(response_b, 'СЕРТ-Б')

    def test_uploaded_certificate_visible_on_upload_site(self):
        response_b = self.client.get(reverse('site_certificates', args=[self.site_b.pk]))
        self.assertContains(response_b, self.cert_uploaded_b.document_number)

        response_a = self.client.get(reverse('site_certificates', args=[self.site_a.pk]))
        self.assertNotContains(response_a, self.cert_uploaded_b.document_number)

    def test_upload_from_site_records_site(self):
        response = self.client.post(
            reverse('site_cert_uploads', args=[self.site_b.pk]),
            {'files': SimpleUploadedFile('scan.pdf', b'%PDF-1.4 test', content_type='application/pdf')},
        )
        upload = CertificateUpload.objects.latest('pk')
        self.assertEqual(upload.site, self.site_b)
        self.assertRedirects(response, reverse('cert_review', args=[upload.pk]),
                             fetch_redirect_response=False)

    def test_upload_from_global_page_has_no_site(self):
        self.client.post(
            reverse('cert_uploads'),
            {'files': SimpleUploadedFile('global.pdf', b'%PDF-1.4 global', content_type='application/pdf')},
        )
        upload = CertificateUpload.objects.latest('pk')
        self.assertIsNone(upload.site)

    def test_site_materials_only_from_own_deliveries(self):
        response_a = self.client.get(reverse('site_materials', args=[self.site_a.pk]))
        self.assertContains(response_a, self.material.name)
        self.assertContains(response_a, reverse('ref_list', args=['materials']))

        response_b = self.client.get(reverse('site_materials', args=[self.site_b.pk]))
        self.assertNotContains(response_b, self.material.name)

    def test_act_check_uses_material_not_site(self):
        """Акт объекта Б использует сертификат материала, поставленного на А: ограничения объектом нет."""
        ActMaterial.objects.create(act=self.act_b, material=self.material, written_off_volume=1)
        materials = get_materials_for_act(self.act_b)
        self.assertEqual(len(materials), 1)
        self.assertIsNone(materials[0]['certificate_problem'])
        self.assertIn(self.cert_delivered.document_number, materials[0]['certificate_text'])
