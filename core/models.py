from django.db import models


def corrected_act_file_path(instance, filename):
    return f'generated_acts/corrected/act_{instance.pk}_{filename}'

class RecordStatus(models.TextChoices):
    ACTIVE = 'active', 'Действует'
    ARCHIVED = 'archived', 'В архиве'



class Documents(models.Model):
    path_to_file = models.FileField(upload_to='documents/')
    file_hash = models.CharField(max_length=200, blank=True, null=True, unique=True)
    document_type = models.CharField(max_length=100)
    text = models.TextField(blank=True)
    auto_generated = models.BooleanField(default=False, verbose_name='Создан автоматически')
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Документ"
        verbose_name_plural = "Документы"

    def __str__(self):
        return str(self.path_to_file)



class Standard(models.Model):
    name = models.CharField(max_length=200)
    designation = models.CharField(max_length=300)
    category = models.CharField(max_length=200, blank=True)
    effective_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=RecordStatus.choices, default=RecordStatus.ACTIVE)
    reference_link = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = "Стандарт"
        verbose_name_plural = "Стандарты"
    
    def __str__(self):
        return self.name

class Counterparty(models.Model):
    short_name = models.CharField(max_length=200)
    full_name = models.CharField(max_length=200, blank=True)
    inn = models.CharField(max_length=200)
    ogrn = models.CharField(max_length=200, blank=True)
    kpp = models.CharField(max_length=200, blank=True)
    address = models.CharField(max_length=300, blank=True)
    role = models.CharField(max_length=200, blank=True)
    contacts = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=RecordStatus.choices, default=RecordStatus.ACTIVE)

    class Meta:
        verbose_name = "Контрагент"
        verbose_name_plural = "Контрагенты"
    
    def __str__(self):
        return self.short_name

class SRO(models.Model):
    organization = models.ForeignKey(Counterparty, on_delete=models.PROTECT)
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=200)
    issue_date = models.DateField()
    status = models.CharField(max_length=20, choices=RecordStatus.choices, default=RecordStatus.ACTIVE)

    def __str__(self):
        return f"{self.name} ({self.organization})"

    class Meta:
        verbose_name = "СРО"
        verbose_name_plural = "СРО"

class Person(models.Model):
    organization = models.ForeignKey(Counterparty, on_delete=models.PROTECT)
    last_name = models.CharField(max_length=200)
    first_name = models.CharField(max_length=200)
    middle_name = models.CharField(max_length=200, blank=True, null=True)
    position = models.CharField(max_length=200, blank=True)
    role = models.CharField(max_length=200, blank=True)
    order_document = models.CharField(max_length=200, blank=True)
    order_document_file = models.ForeignKey(Documents, on_delete=models.PROTECT, null=True, blank=True, related_name="person_orders")
    authority_end_date = models.DateField(null=True, blank=True)
    nrs_id = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=20, choices=RecordStatus.choices, default=RecordStatus.ACTIVE)

    class Meta:
        verbose_name = "Участник"
        verbose_name_plural = "Участники"
    
    def __str__(self):
        return f"{self.last_name} {self.first_name}"

    @property
    def display_name(self):
        """ФИО + организация и должность — чтобы в списках различать однофамильцев."""
        details = [str(self.organization)] if self.organization_id else []
        if self.position:
            details.append(self.position)
        if self.role and self.role != self.position:
            details.append(self.role)
        return f'{self} ({", ".join(details)})' if details else str(self)

class Material(models.Model):
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=200, blank=True)
    manufacturer = models.ForeignKey(Counterparty, on_delete=models.PROTECT, null=True, blank=True)
    unit = models.CharField(max_length=10, blank=True)
    standard = models.ForeignKey(Standard, on_delete=models.PROTECT, null=True, blank=True)
    status = models.CharField(max_length=20, choices=RecordStatus.choices, default=RecordStatus.ACTIVE)

    class Meta:
        verbose_name = "Материал"
        verbose_name_plural = "Материалы"
    
    def __str__(self):
        return self.name

class ConstructionSite(models.Model):
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=200)
    code = models.CharField(max_length=200, blank=True)
    customer = models.ForeignKey(Counterparty, on_delete=models.PROTECT, related_name='customer_sites')
    general_contractor = models.ForeignKey(Counterparty, on_delete=models.PROTECT, related_name='contractor_sites')
    designer = models.ForeignKey(Counterparty, on_delete=models.PROTECT, related_name='designer_sites')
    responsible_person = models.ForeignKey(Person, on_delete=models.PROTECT, blank=True, null=True)
    registry_submitted_by = models.ForeignKey(
        Person, on_delete=models.PROTECT, blank=True, null=True,
        related_name='registry_submitted_sites', verbose_name='Реестр к АОСР: сдал на проверку')
    registry_accepted_by = models.ForeignKey(
        Person, on_delete=models.PROTECT, blank=True, null=True,
        related_name='registry_accepted_sites', verbose_name='Реестр к АОСР: принял на проверку')
    registry_approved_by = models.ForeignKey(
        Person, on_delete=models.PROTECT, blank=True, null=True,
        related_name='registry_approved_sites', verbose_name='Реестр к АОСР: согласовал')
    construction_start = models.DateField(null=True, blank=True)
    planned_completion = models.DateField(null=True, blank=True)
    actual_completion = models.DateField(blank=True, null=True)
    design_doc_reference = models.CharField(max_length=300, blank=True, verbose_name="Реквизиты проектной документации (по умолчанию для всех актов)")
    contract_reference = models.CharField(max_length=300, blank=True, verbose_name="Договор подряда")

    class Meta:
        verbose_name = "Объект"
        verbose_name_plural = "Объекты"
    
    def __str__(self):
        return self.name
    
class Room(models.Model):
    project_number = models.CharField(max_length=100, blank=True)
    floor = models.CharField(max_length=20, blank=True)
    building = models.CharField(max_length=40, blank=True)
    explication_name = models.CharField(max_length=200)
    site = models.ForeignKey(ConstructionSite, on_delete=models.PROTECT)

    class Meta:
        verbose_name = "Помещение"
        verbose_name_plural = "Помещения"
    
    def __str__(self):
        return self.explication_name
    
class DocumentationSection(models.Model):
    site = models.ForeignKey(ConstructionSite, on_delete=models.PROTECT)
    project_code = models.CharField(max_length=200, blank=True)
    estimate_code = models.CharField(max_length=200, blank=True)
    name = models.CharField(max_length=200)
    order_number = models.IntegerField(null=True, blank=True)
    standard = models.ForeignKey(Standard, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=RecordStatus.choices, default=RecordStatus.ACTIVE)

    class Meta:
        verbose_name = "Раздел документации"
        verbose_name_plural = "Разделы документации"
    
    def __str__(self):
        return self.name
    
class WorkType(models.Model):
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=200, blank=True)
    unit = models.CharField(max_length=10, blank=True, verbose_name="Ед. изм.")
    section = models.ForeignKey(DocumentationSection, on_delete=models.PROTECT)
    requires_hidden_work_act = models.BooleanField()
    standard =  models.ForeignKey(Standard, on_delete=models.PROTECT)
    allowed_deviation_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    order_number = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=RecordStatus.choices, default=RecordStatus.ACTIVE)

    class Meta:
        verbose_name = "Вид работы"
        verbose_name_plural = "Виды работы"
    
    def __str__(self):
        return self.name




class Certificate(models.Model):
    """Документ о качестве материала."""

    class Status(models.TextChoices):
        AUTO_IDENTIFIED = 'auto identified', 'автоматически опознано'
        CONFIRMED = 'confirmed', 'подтверждено'
        REJECTED = 'rejected', 'отклонена'

    class DocType(models.TextChoices):
        CERTIFICATE = 'Сертификат соответствия', 'Сертификат соответствия'
        DECLARATION = 'Декларация о соответствии', 'Декларация о соответствии'
        REFUSAL = 'Отказное письмо', 'Отказное письмо'
        QUALITY_PASSPORT = 'Паспорт качества', 'Паспорт качества'
        INFORMATION_LETTER = 'Информационное письмо', 'Информационное письмо'
        TEST_PROTOCOL = 'Протокол испытаний', 'Протокол испытаний'

    class Source(models.TextChoices):
        MANUAL = 'manual', 'введён вручную'
        DOCUMENT = 'document', 'распознан с документа'
        REGISTRY = 'registry', 'из реестра Росаккредитации'
        MOSSTROICERT = 'mosstroicert', 'из реестра Мосстройсертификации'

    class RegistryStatus(models.TextChoices):
        ACTIVE = 'active', 'действует'
        ENDED = 'ended', 'прекращён'
        SUSPENDED = 'suspended', 'приостановлен'
        NOT_FOUND = 'not_found', 'не найден в реестре'

    document_number = models.CharField(max_length=200)
    document_type = models.CharField(max_length=200, choices=DocType.choices, default=DocType.CERTIFICATE)
    manufacturer = models.ForeignKey(Counterparty, on_delete=models.PROTECT, null=True, blank=True)
    issue_date = models.DateField()
    expiry_date = models.DateField(null=True, blank=True, verbose_name='Действует до')
    sheet_count = models.PositiveSmallIntegerField(null=True, blank=True, verbose_name='Кол-во листов')
    verification_status = models.CharField(max_length=30, choices=Status.choices, default=Status.AUTO_IDENTIFIED)
    document = models.ForeignKey(Documents, on_delete=models.PROTECT, null=True, blank=True)

    # откуда данные и что о документе известно (заполняется при распознавании)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL, verbose_name='Откуда данные')
    pages = models.CharField(max_length=50, blank=True, verbose_name='Страницы в файле')
    product_text = models.TextField(blank=True, verbose_name='Продукция (как в документе)')
    issuer = models.CharField(max_length=300, blank=True, verbose_name='Кто выдал / лаборатория')
    registry_url = models.URLField(max_length=500, blank=True, verbose_name='Запись в реестре')
    registry_record_id = models.CharField(max_length=100, blank=True, verbose_name='ID записи в реестре')
    check_url = models.URLField(max_length=500, blank=True, verbose_name='Ссылка для проверки подлинности')
    registry_status = models.CharField(max_length=20, choices=RegistryStatus.choices, blank=True,
                                       verbose_name='Статус в реестре')
    registry_checked_at = models.DateTimeField(null=True, blank=True, verbose_name='Проверен в реестре')

    class Meta:
        verbose_name = "Сертификат"
        verbose_name_plural = "Сертификаты"

    def __str__(self):
        return f'{self.document_type} №{self.document_number}'

    def materials_label(self, limit=3):
        """Названия материалов, к которым привязан документ, — чтобы не запоминать номера наизусть."""
        names = list(dict.fromkeys(
            mc.material.name for mc in self.materialcertificate_set.all() if mc.material_id
        ))
        if not names:
            return ''
        shown = ', '.join(names[:limit])
        if len(names) > limit:
            shown += f' и ещё {len(names) - limit}'
        return shown

    @classmethod
    def requires_expiry(cls, document_type):
        return document_type in (cls.DocType.CERTIFICATE, cls.DocType.DECLARATION)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.requires_expiry(self.document_type) and not self.expiry_date:
            raise ValidationError({'expiry_date': 'Для сертификата и декларации нужен срок действия.'})
        if self.expiry_date and self.issue_date and self.expiry_date < self.issue_date:
            raise ValidationError({'expiry_date': 'Срок действия раньше даты выдачи.'})


class CertificateUpload(models.Model):
    """Загруженный файл с документами о качестве и результат его распознавания."""

    class Status(models.TextChoices):
        QUEUED = 'queued', 'в очереди'
        PROCESSING = 'processing', 'распознаётся'
        DONE = 'done', 'готово'
        ERROR = 'error', 'ошибка'

    document = models.ForeignKey(Documents, on_delete=models.CASCADE, verbose_name='Файл')
    site = models.ForeignKey(
        ConstructionSite, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='certificate_uploads', verbose_name='Объект (происхождение)')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    progress = models.CharField(max_length=200, blank=True)
    error = models.TextField(blank=True)
    worker_heartbeat = models.DateTimeField(null=True, blank=True)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Загрузка документов о качестве"
        verbose_name_plural = "Загрузки документов о качестве"
        ordering = ['-created_at']

    def __str__(self):
        return f"Загрузка №{self.pk} ({self.get_status_display()})"


class MaterialCertificate(models.Model):
    material = models.ForeignKey(Material, on_delete=models.PROTECT)
    certificate = models.ForeignKey(Certificate, on_delete=models.PROTECT)

    class Meta:
        verbose_name = "Сертификат материала"
        verbose_name_plural = "Сертификаты материалов"
    
    def __str__(self):
        return f"{self.material} - {self.certificate}"

class DeliveryNote(models.Model):
    class Status(models.TextChoices):
        ACCEPTED = 'accepted', 'принята'
        ACCEPTED_WITH_REMARKS = 'accepted with remarks', 'принята с замечаниями'
        REJECTED = 'rejected', 'отклонена'

    number = models.CharField(max_length=200)
    delivery_date = models.DateField()
    site = models.ForeignKey(ConstructionSite, on_delete=models.PROTECT)
    supplier = models.ForeignKey(Counterparty, on_delete=models.PROTECT)
    received_by = models.ForeignKey(Person, on_delete=models.PROTECT, null=True, blank=True)
    acceptance_status = models.CharField(max_length=100, choices=Status.choices, blank=True)
    has_quality_passport = models.BooleanField()
    notes = models.TextField(blank=True)
    document = models.ForeignKey(Documents, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='delivery_notes', verbose_name='Скан накладной')
    
    class Meta:
        verbose_name = "Накладная"
        verbose_name_plural = "Накладные"
    
    def __str__(self):
        return self.number
    
class DeliveryItem(models.Model):
    class Status(models.TextChoices):
        UNIDENTIFIED = 'unidentified', 'не опознано'
        AUTO_IDENTIFIED = 'auto identified', 'автоматически опознано'
        CONFIRMED = 'confirmed', 'подтверждено'

    delivery_note = models.ForeignKey(DeliveryNote, on_delete=models.PROTECT)
    material = models.ForeignKey(Material, on_delete=models.PROTECT, null=True, blank=True)
    original_text = models.CharField(max_length=300)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    unit = models.CharField(max_length=20, blank=True, verbose_name="Ед. изм.")
    certificate_text = models.TextField(blank=True, verbose_name="Сопроводительный документ (текст из таблицы)")
    batch_number = models.CharField(max_length=100, blank=True)
    matching_status = models.CharField(max_length=30, choices=Status.choices, default=Status.UNIDENTIFIED)

    class Meta:
        verbose_name = "Позиция поставки"
        verbose_name_plural = "Позиции поставки"
    
    def __str__(self):
        # Пока строка не сопоставлена, показываем то, как она написана в накладной,
        # а не «None»: так её можно узнать в списке и выбрать вручную.
        what = self.material or self.original_text or 'Позиция без названия'
        if self.quantity is None:
            return str(what)
        qty = f'{self.quantity} {self.unit}'.strip()
        return f'{what} ({qty})'
    
class IncomingInspection(models.Model):
    delivery_item = models.ForeignKey(DeliveryItem, on_delete=models.PROTECT)
    inspection_date = models.DateField(null=True, blank=True, verbose_name="Дата контроля")
    inspector = models.ForeignKey(Person, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Проверил")
    inspector_name = models.CharField(max_length=200, blank=True,
                                      verbose_name="Подпись лица, осуществляющего контроль")
    control_type = models.CharField(max_length=200, blank=True, verbose_name="Вид контроля")
    inspection_result = models.CharField(max_length=200, blank=True, verbose_name="Результат контроля")
    usage_permission = models.BooleanField()

    class Meta:
        verbose_name = "Входной контроль"
        verbose_name_plural = "Входной контроль"
    
    def __str__(self):
        return f"{self.delivery_item} ({self.inspection_date})"



class EstimateLine(models.Model):
    room = models.ForeignKey(Room, on_delete=models.PROTECT)
    work_type = models.ForeignKey(WorkType, on_delete=models.PROTECT)
    estimate_code = models.CharField(max_length=200, blank=True)
    ks2_position = models.CharField(max_length=200, blank=True)
    estimated_volume = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = "Сметная строка"
        verbose_name_plural = "Сметные строки"

    def __str__(self):
        return f"{self.room} ({self.work_type})"

class HiddenWorkAct(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Черновик'
        PENDING = 'PENDING', 'На подписании'
        PRINTING = 'PRINTING', 'На печати'
        ACCEPTED = 'ACCEPTED', 'Подписан'
        REJECTED = 'REJECTED', 'Отклонен'

    act_number = models.CharField(max_length=200)
    drawn_up_date = models.DateField()
    section = models.ForeignKey(DocumentationSection, on_delete=models.PROTECT)
    # шапка фиксируется снимком при создании акта; правки скрытого блока пишутся только сюда
    header_snapshot = models.JSONField(default=dict, blank=True, verbose_name="Шапка акта (снимок)")
    status = models.CharField(max_length=100, choices=Status.choices, default=Status.DRAFT)
    next_work_type = models.ForeignKey(
    'WorkType',
    on_delete=models.SET_NULL, null=True, blank=True, related_name='permitted_after_acts',verbose_name="Разрешены работы (из справочника)")
    next_work_override = models.CharField(max_length=300, blank=True, verbose_name="Разрешены работы (свой текст)")
    corrected_file = models.FileField(upload_to=corrected_act_file_path, blank=True,
                                      verbose_name='Исправленный файл (прикрепляется вручную)')
    copies_count = models.PositiveIntegerField(default=4, null=True, blank=True,
                                               verbose_name='Экземпляров акта')
    protocols = models.ManyToManyField(
        Certificate, blank=True, related_name='protocol_acts',
        limit_choices_to={'document_type': Certificate.DocType.TEST_PROTOCOL},
        verbose_name='Протоколы испытаний')

    class Meta:
        verbose_name = "Акт ОСР"
        verbose_name_plural = "Акты ОСР"

    def __str__(self):
        return f"{self.act_number} ({self.drawn_up_date})"
    
class ActSigner(models.Model):
    class Role(models.TextChoices):
        CUSTOMER = 'customer', 'Представитель застройщика или заказчика'
        CUSTOMER_CONTROL = 'customer_control', 'Представитель застройщика по стройконтролю'
        CONTRACTOR = 'contractor', 'Представитель лица, осуществляющего строительство'
        CONTRACTOR_CONTROL = 'contractor_control', 'Представитель подрядчика по стройконтролю'
        DESIGNER = 'designer', 'Представитель проектировщика (ГАП)'
        WORK_PERFORMER = 'work_performer', 'Представитель исполнителя работ'
        OTHER = 'other', 'Иной представитель'

    act = models.ForeignKey(HiddenWorkAct, on_delete=models.CASCADE)
    person = models.ForeignKey(Person, on_delete=models.PROTECT)
    role_in_act = models.CharField(max_length=30, choices=Role.choices)

    class Meta:
        verbose_name = "Подписант акта"
        verbose_name_plural = "Подписанты акта"
        
    def __str__(self):
        return f"{self.person} ({self.act})"
    
class SiteSigner(models.Model):
    """Подписант по умолчанию для актов этого объекта.
    Копируется в акт при его создании; изменения здесь НИКОГДА не меняют уже созданные акты."""
    site = models.ForeignKey(ConstructionSite, on_delete=models.PROTECT)
    person = models.ForeignKey(Person, on_delete=models.PROTECT)
    role_in_act = models.CharField(max_length=30, choices=ActSigner.Role.choices)

    class Meta:
        verbose_name = "Подписант объекта"
        verbose_name_plural = "Подписанты объекта"
        unique_together = ('site', 'role_in_act')

    def __str__(self):
        return f"{self.person} ({self.site})"


class ActMaterial(models.Model):
    act = models.ForeignKey(HiddenWorkAct, on_delete=models.PROTECT)
    delivery_item = models.ForeignKey(DeliveryItem, on_delete=models.PROTECT, null=True, blank=True)
    material = models.ForeignKey(Material, on_delete=models.PROTECT, null=True, blank=True)
    written_off_volume = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        verbose_name = "Использованные материалы в акте"
        verbose_name_plural = "Использованные материалы в актах"

    def clean(self):
        from django.core.exceptions import ValidationError
        if bool(self.delivery_item_id) == bool(self.material_id):
            raise ValidationError('Укажите либо позицию поставки, либо материал без накладной — одно из двух.')

    def __str__(self):
        return f"{self.act} ({self.delivery_item or self.material})"

class ActAttachment(models.Model):
    act = models.ForeignKey(HiddenWorkAct, on_delete=models.PROTECT)
    document = models.ForeignKey(Documents, on_delete=models.PROTECT)

    class Meta:
        verbose_name = "Приложение к акту"
        verbose_name_plural = "Приложение к акту"
        
    def __str__(self):
        return f"Приложение к акту {self.act}"

class DeviationResolution(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'На подписании'
        ACCEPTED = 'ACCEPTED', 'Согласовано'
        REJECTED = 'REJECTED', 'Отклонен'

    estimate_line = models.ForeignKey(EstimateLine, on_delete=models.PROTECT)
    deviation_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    decision_date = models.DateField(null=True, blank=True)
    prepared_by = models.ForeignKey(Person, on_delete=models.PROTECT, null=True, blank=True)
    status = models.CharField(max_length=200, choices=Status.choices, default=Status.PENDING)
    supporting_document = models.ForeignKey(Documents, on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        verbose_name = "Решение по отклонению"
        verbose_name_plural = "Решения по отклонениям"
        
    def __str__(self):
        return f"{self.estimate_line} ({self.deviation_amount})"

class WorkVolume(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Черновик'
        PENDING = 'PENDING', 'На подписании'
        ACCEPTED = 'ACCEPTED', 'Подписан'
        REJECTED = 'REJECTED', 'Отклонен'

    work_type = models.ForeignKey(WorkType, on_delete=models.PROTECT)
    room = models.ForeignKey(Room, on_delete=models.PROTECT, null=True, blank=True)
    location = models.CharField(max_length=300, blank=True, verbose_name="Место выполнения (оси, отметка)")
    performed_by = models.ForeignKey(Person, on_delete=models.PROTECT, null=True, blank=True)
    act = models.ForeignKey(HiddenWorkAct, on_delete=models.SET_NULL, null=True, blank=True)
    performance_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    actual_volume = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    unit = models.CharField(max_length=20, blank=True)
    acceptance_status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        verbose_name = "Объем работы"
        verbose_name_plural = "Объемы работ"

    def __str__(self):
        return f"{self.work_type} ({self.location or self.room or 'локация не указана'})"


class MaterialAlias(models.Model):
    """Как материал пишут в накладных. Запоминается при ручном сопоставлении,
    чтобы в следующий раз строка «Лист ГКЛ Кнауф 12.5» сама нашла нужный материал."""
    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name='aliases')
    text = models.CharField(max_length=300, verbose_name='Как написано в накладной')
    normalized = models.CharField(max_length=300, unique=True, editable=False)

    class Meta:
        verbose_name = "Синоним материала"
        verbose_name_plural = "Синонимы материалов"

    def __str__(self):
        return f"{self.text} → {self.material}"

    @staticmethod
    def normalize(text):
        import re
        text = (text or '').lower().replace('ё', 'е')
        text = re.sub(r'(?<=\d),(?=\d)', '.', text)              # 12,5 → 12.5
        text = re.sub(r'(?<!\d)\.|\.(?!\d)', ' ', text)            # точка не между цифрами — разделитель
        return re.sub(r'\s+', ' ', re.sub(r'[^0-9a-zа-я.×/ ]+', ' ', text)).strip()

    def save(self, *args, **kwargs):
        self.normalized = self.normalize(self.text)
        super().save(*args, **kwargs)


class EventLog(models.Model):
    """Журнал событий: кто и когда что сделал. Пишется через core/events.py."""
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Когда')
    user = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Кто')
    action = models.CharField(max_length=100, verbose_name='Действие')
    object_repr = models.CharField(max_length=300, blank=True, verbose_name='Объект')
    details = models.TextField(blank=True, verbose_name='Подробности')

    class Meta:
        verbose_name = "Событие"
        verbose_name_plural = "Журнал событий"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.created_at:%d.%m.%Y %H:%M} {self.action}"
