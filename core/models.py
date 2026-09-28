from django.db import models

class RecordStatus(models.TextChoices):
    ACTIVE = 'active', 'Действует'
    ARCHIVED = 'archived', 'В архиве'



class Documents(models.Model):
    path_to_file = models.FileField(upload_to='documents/')
    file_hash = models.CharField(max_length=200, blank=True, null=True, unique=True)
    document_type = models.CharField(max_length=100)
    text = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Документ"
        verbose_name_plural = "Документы"

    def __str__(self):
        return str(self.path_to_file)



class Standard(models.Model):
    name = models.CharField(max_length=200)
    designation = models.CharField(max_length=300)
    category = models.CharField(max_length=200)
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
    role = models.CharField(max_length=200)
    contacts = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=RecordStatus.choices, default=RecordStatus.ACTIVE)

    class Meta:
        verbose_name = "Контрагент"
        verbose_name_plural = "Контрагенты"
    
    def __str__(self):
        return self.short_name

class Person(models.Model):
    organization = models.ForeignKey(Counterparty, on_delete=models.PROTECT)
    last_name = models.CharField(max_length=200)
    first_name = models.CharField(max_length=200)
    middle_name = models.CharField(max_length=200, blank=True, null=True)
    position = models.CharField(max_length=200, blank=True)
    role = models.CharField(max_length=200, blank=True)
    order_document = models.CharField(max_length=200, blank=True)
    authority_end_date = models.DateField(null=True, blank=True)
    nrs_id = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=RecordStatus.choices, default=RecordStatus.ACTIVE)

    class Meta:
        verbose_name = "Участник"
        verbose_name_plural = "Участники"
    
    def __str__(self):
        return f"{self.last_name} {self.first_name}"

class Material(models.Model):
    name = models.CharField(max_length=200)
    standard = models.ForeignKey(Standard, on_delete=models.PROTECT)
    manufacturer = models.ForeignKey(Counterparty, on_delete=models.PROTECT)
    unit = models.CharField(max_length=10)
    code = models.CharField(max_length=200)
    category = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=RecordStatus.choices, default=RecordStatus.ACTIVE)

    class Meta:
        verbose_name = "Материал"
        verbose_name_plural = "Материалы"
    
    def __str__(self):
        return self.name

class ConstructionSite(models.Model):
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=200)
    code = models.CharField(max_length=200)
    customer = models.ForeignKey(Counterparty, on_delete=models.PROTECT, related_name='customer_sites')
    general_contractor = models.ForeignKey(Counterparty, on_delete=models.PROTECT, related_name='contractor_sites')
    responsible_person = models.ForeignKey(Person, on_delete=models.PROTECT)
    construction_start = models.DateField()
    planned_completion = models.DateField()
    actual_completion = models.DateField(blank=True, null=True)

    class Meta:
        verbose_name = "Объект"
        verbose_name_plural = "Объекты"
    
    def __str__(self):
        return self.name
    
class Room(models.Model):
    project_number = models.CharField(max_length=100)
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
    project_code = models.CharField(max_length=200)
    estimate_code = models.CharField(max_length=200)
    name = models.CharField(max_length=200)
    order_number = models.IntegerField()
    standard = models.ForeignKey(Standard, on_delete=models.PROTECT)
    status = models.CharField(max_length=20, choices=RecordStatus.choices, default=RecordStatus.ACTIVE)

    class Meta:
        verbose_name = "Раздел документации"
        verbose_name_plural = "Разделы документации"
    
    def __str__(self):
        return self.name
    
class WorkType(models.Model):
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=200)
    unit = models.CharField(max_length=10)
    section = models.ForeignKey(DocumentationSection, on_delete=models.PROTECT)
    requires_hidden_work_act = models.BooleanField()
    standard =  models.ForeignKey(Standard, on_delete=models.PROTECT)
    allowed_deviation_percent = models.DecimalField(max_digits=5, decimal_places=2)
    order_number = models.IntegerField()
    status = models.CharField(max_length=20, choices=RecordStatus.choices, default=RecordStatus.ACTIVE)

    class Meta:
        verbose_name = "Вид работы"
        verbose_name_plural = "Виды работы"
    
    def __str__(self):
        return self.name




class Certificate(models.Model):
    class Status(models.TextChoices):
        AUTO_IDENTIFIED = 'auto identified', 'автоматически опознано'
        CONFIRMED = 'confirmed', 'подтверждено'
        REJECTED = 'rejected', 'отклонена'

    document_number = models.CharField(max_length=200)
    document_type = models.CharField(max_length=200)
    manufacturer = models.ForeignKey(Counterparty, on_delete=models.PROTECT)
    issue_date = models.DateField()
    expiry_date = models.DateField()
    verification_status = models.CharField(max_length=30, choices=Status.choices, default=Status.AUTO_IDENTIFIED)
    document = models.ForeignKey(Documents, on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        verbose_name = "Сертификат"
        verbose_name_plural = "Сертификаты"
    
    def __str__(self):
        return self.document_number

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
    received_by = models.ForeignKey(Person, on_delete=models.PROTECT)
    acceptance_status = models.CharField(max_length=100, choices=Status.choices)
    has_quality_passport = models.BooleanField()
    notes = models.TextField(blank=True)
    
    class Meta:
        verbose_name = "Накладная"
        verbose_name_plural = "Накладные"
    
    def __str__(self):
        return self.number
    
class  DeliveryItem(models.Model):
    class Status(models.TextChoices):
        UNIDENTIFIED = 'unidentified', 'не опознано'
        AUTO_IDENTIFIED = 'auto identified', 'автоматически опознано'
        CONFIRMED = 'confirmed', 'подтверждено'

    delivery_note = models.ForeignKey(DeliveryNote, on_delete=models.PROTECT)
    material = models.ForeignKey(Material, on_delete=models.PROTECT, null=True, blank=True)
    original_text = models.CharField(max_length=300)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=20)
    batch_number = models.CharField(max_length=100, blank=True)
    matching_status = models.CharField(max_length=30, choices=Status.choices, default=Status.UNIDENTIFIED)

    class Meta:
        verbose_name = "Позиция поставки"
        verbose_name_plural = "Позиции поставки"
    
    def __str__(self):
        return f"{self.material} ({self.quantity})"
    
class IncomingInspection(models.Model):
    delivery_item = models.ForeignKey(DeliveryItem, on_delete=models.PROTECT)
    inspection_date = models.DateField()
    inspector = models.ForeignKey(Person, on_delete=models.PROTECT)
    inspection_result = models.CharField(max_length=200)
    usage_permission = models.BooleanField()

    class Meta:
        verbose_name = "Входной контроль"
    
    def __str__(self):
        return f"{self.delivery_item} ({self.inspection_date})"



class EstimateLine(models.Model):
    room = models.ForeignKey(Room, on_delete=models.PROTECT)
    work_type = models.ForeignKey(WorkType, on_delete=models.PROTECT)
    estimate_code = models.CharField(max_length=200)
    ks2_position = models.CharField(max_length=200)
    estimated_volume = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "Сметная строка"
        verbose_name_plural = "Сметные строки"

    def __str__(self):
        return f"{self.room} ({self.work_type})"

class HiddenWorkAct(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Черновик'
        PENDING = 'PENDING', 'На подписании'
        ACCEPTED = 'ACCEPTED', 'Подписан'
        REJECTED = 'REJECTED', 'Отклонен'    

    act_number = models.CharField(max_length=200)
    drawn_up_date = models.DateField()
    section = models.ForeignKey(DocumentationSection, on_delete=models.PROTECT)
    status = models.CharField(max_length=100, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        verbose_name = "Акт ОСР"
        verbose_name_plural = "Акты ОСР"

    def __str__(self):
        return f"{self.act_number} ({self.drawn_up_date})"
    
class ActSigner(models.Model):
    act = models.ForeignKey(HiddenWorkAct, on_delete=models.PROTECT)
    person = models.ForeignKey(Person, on_delete=models.PROTECT)
    role_in_act = models.CharField(max_length=200)

    class Meta:
        verbose_name = "Подписант акта"
        verbose_name_plural = "Подписанты акта"
        
    def __str__(self):
        return f"{self.person} ({self.act})"
    
class ActMaterial(models.Model):
    act = models.ForeignKey(HiddenWorkAct, on_delete=models.PROTECT)
    delivery_item = models.ForeignKey(DeliveryItem, on_delete=models.PROTECT)
    written_off_volume = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "Использованные материалы в акте"
        
    def __str__(self):
        return f"{self.act} ({self.delivery_item})"

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
    deviation_amount = models.DecimalField(max_digits=10, decimal_places=2)
    decision_date = models.DateField()
    prepared_by = models.ForeignKey(Person, on_delete=models.PROTECT)
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
    room = models.ForeignKey(Room, on_delete=models.PROTECT)
    performed_by = models.ForeignKey(Person, on_delete=models.PROTECT)
    act = models.ForeignKey(HiddenWorkAct, on_delete=models.SET_NULL, null=True, blank=True)
    performance_date = models.DateField()
    actual_volume = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=20)
    acceptance_status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        verbose_name = "Объем работы"
        verbose_name_plural = "Объемы работ"
        
    def __str__(self):
        return f"{self.work_type} ({self.room})"
