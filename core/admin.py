from django.contrib import admin
from .models import (
    Documents, Standard, Counterparty, Person, Material,
    ConstructionSite, Room, DocumentationSection, WorkType,
    Certificate, MaterialCertificate, DeliveryNote, DeliveryItem,
    IncomingInspection, EstimateLine, HiddenWorkAct, ActSigner,
    ActMaterial, ActAttachment, DeviationResolution, WorkVolume,
)

admin.site.register(Documents)
admin.site.register(Standard)
admin.site.register(Counterparty)
admin.site.register(Person)
admin.site.register(Material)
admin.site.register(ConstructionSite)
admin.site.register(Room)
admin.site.register(DocumentationSection)
admin.site.register(WorkType)
admin.site.register(Certificate)
admin.site.register(MaterialCertificate)
admin.site.register(DeliveryNote)
admin.site.register(DeliveryItem)
admin.site.register(IncomingInspection)
admin.site.register(EstimateLine)
admin.site.register(HiddenWorkAct)
admin.site.register(ActSigner)
admin.site.register(ActMaterial)
admin.site.register(ActAttachment)
admin.site.register(DeviationResolution)
admin.site.register(WorkVolume)