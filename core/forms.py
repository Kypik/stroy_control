"""
core/forms.py

Общая фабрика форм для интерфейса инженера:
- русские подписи для полей, у которых в модели нет verbose_name;
- календарь для полей с датой;
- хеш файла и защита от повторной загрузки одного и того же документа.
"""

import hashlib

from django import forms
from django.db import models
from django.forms import modelform_factory, inlineformset_factory
from django.forms.models import ModelChoiceIterator
from django.urls import reverse

NO_CATEGORY = 'Без категории'


class _GroupedIterator(ModelChoiceIterator):
    """Варианты выпадающего списка, сгруппированные по категориям (<optgroup>)."""
    group_key = None

    def __iter__(self):
        if self.field.empty_label is not None:
            yield ('', self.field.empty_label)
        groups = {}
        for obj in self.queryset:
            key = (self.group_key(obj) or '').strip() or NO_CATEGORY
            groups.setdefault(key, []).append(self.choice(obj))
        for key in sorted(groups, key=lambda k: (k == NO_CATEGORY, k.lower())):
            yield (key, groups[key])


def group_choices(formfield, key):
    """Включает группировку вариантов у поля-ссылки. key(obj) -> название группы."""
    formfield.iterator = type('Grouped', (_GroupedIterator,), {'group_key': staticmethod(key)})
    formfield.widget.choices = formfield.choices


FIELD_LABELS = {
    # общее
    'name': 'Название', 'address': 'Адрес', 'code': 'Код / шифр', 'status': 'Статус',
    'category': 'Категория', 'role': 'Роль', 'unit': 'Ед. изм.', 'number': 'Номер',
    'notes': 'Примечание', 'site': 'Объект', 'section': 'Раздел документации',
    # контрагенты, СРО, люди
    'short_name': 'Краткое наименование', 'full_name': 'Полное наименование',
    'inn': 'ИНН', 'ogrn': 'ОГРН', 'kpp': 'КПП', 'contacts': 'Контакты',
    'organization': 'Организация', 'issue_date': 'Дата выдачи',
    'last_name': 'Фамилия', 'first_name': 'Имя', 'middle_name': 'Отчество',
    'position': 'Должность', 'order_document': 'Приказ / доверенность (номер, дата)',
    'order_document_file': 'Скан приказа', 'authority_end_date': 'Полномочия действуют до',
    'nrs_id': 'Номер в НРС',
    # нормативы, материалы
    'designation': 'Обозначение (ГОСТ/СП)', 'effective_date': 'Дата введения',
    'reference_link': 'Ссылка', 'standard': 'Норматив (ГОСТ/СП)',
    'manufacturer': 'Производитель', 'material': 'Материал',
    # объект
    'customer': 'Застройщик / заказчик', 'general_contractor': 'Генподрядчик',
    'designer': 'Проектировщик', 'responsible_person': 'Ответственный',
    'construction_start': 'Начало строительства', 'planned_completion': 'Плановая сдача',
    'actual_completion': 'Фактическая сдача',
    'design_doc_reference': 'Реквизиты проектной документации (для всех актов)',
    'contract_reference': 'Договор подряда',
    'project_number': 'Номер по проекту', 'floor': 'Этаж', 'building': 'Корпус',
    'explication_name': 'Наименование по экспликации',
    'project_code': 'Шифр по проекту', 'estimate_code': 'Шифр по смете',
    'order_number': 'Порядковый номер', 'requires_hidden_work_act': 'Нужен акт ОСР',
    'allowed_deviation_percent': 'Допустимое отклонение, %',
    # документы и сертификаты
    'path_to_file': 'Файл', 'file_hash': 'Хеш файла', 'text': 'Распознанный текст',
    'uploaded_at': 'Загружен', 'document_number': 'Номер документа',
    'document_type': 'Тип документа', 'expiry_date': 'Действует до',
    'verification_status': 'Статус проверки', 'document': 'Документ (файл)',
    'certificate': 'Сертификат',
    # поставки
    'delivery_date': 'Дата поставки', 'supplier': 'Поставщик', 'received_by': 'Принял',
    'acceptance_status': 'Статус приёмки', 'has_quality_passport': 'Есть паспорт качества',
    'delivery_note': 'Накладная', 'original_text': 'Как написано в накладной',
    'quantity': 'Количество', 'batch_number': 'Номер партии',
    'matching_status': 'Статус сопоставления', 'delivery_item': 'Позиция поставки',
    'inspection_date': 'Дата контроля', 'inspector': 'Проверил',
    'inspection_result': 'Результат осмотра', 'usage_permission': 'Разрешено к применению',
    # объёмы и акты
    'room': 'Помещение', 'location': 'Место выполнения (оси, отметка)',
    'work_type': 'Вид работ', 'ks2_position': 'Позиция КС-2',
    'estimated_volume': 'Объём по смете', 'performed_by': 'Кто выполнял',
    'performance_date': 'Дата начала', 'end_date': 'Дата окончания',
    'actual_volume': 'Объём по факту',
    'act': 'Акт', 'act_number': 'Номер акта', 'drawn_up_date': 'Дата акта',
    'next_work_type': 'Разрешены работы (из справочника)',
    'next_work_override': 'Разрешены работы (свой текст)',
    'person': 'Участник', 'role_in_act': 'Роль в акте', 'written_off_volume': 'Списано в акт',
    'estimate_line': 'Сметная строка', 'deviation_amount': 'Величина отклонения',
    'decision_date': 'Дата решения', 'prepared_by': 'Составитель',
    'supporting_document': 'Документ-основание',
}


def field_label(model_field):
    """Подпись поля: verbose_name из модели, если он задан вручную, иначе из словаря выше."""
    auto = model_field.name.replace('_', ' ')
    if str(model_field.verbose_name) != auto:
        return str(model_field.verbose_name).capitalize()
    return FIELD_LABELS.get(model_field.name, auto.capitalize())


def _document_label(doc):
    """В выпадающих списках документ показывается как «тип — имя файла», а не путём."""
    file_name = ''
    for f in doc._meta.fields:
        if isinstance(f, models.FileField):
            value = getattr(doc, f.name)
            file_name = value.name.rsplit('/', 1)[-1] if value else ''
            break
    kind = getattr(doc, 'document_type', '') or 'Документ'
    return f"{kind} — {file_name}" if file_name else kind


def _section_label(section):
    """Раздел без объекта неоднозначен: «Раздел 1» есть на каждом объекте."""
    return f'{section.name} — {section.site.name}'


def _act_label(act):
    return (f'№{act.act_number} от {act.drawn_up_date:%d.%m.%Y} — '
            f'{act.section.name} ({act.section.site.name})')


def _estimate_line_label(line):
    return f'{line.room} ({line.room.site.name}) — {line.work_type}'


def _delivery_item_label(item):
    """Позиция поставки узнаваема по материалу, количеству и накладной, а не по номеру записи."""
    what = item.material or item.original_text or 'Позиция'
    qty = f'{item.quantity} {item.unit}'.strip() if item.quantity is not None else ''
    note = item.delivery_note
    parts = [str(what), qty]
    if note:
        parts.append(f'накл. №{note.number} от {note.delivery_date:%d.%m.%Y}')
    return ' · '.join(p for p in parts if p)


def _protocol_label(cert):
    """Подпись протокола в списке выбора: номер, дата и изготовитель, а не только номер."""
    parts = [f"№ {cert.document_number}"]
    if cert.issue_date:
        parts.append(f"от {cert.issue_date:%d.%m.%Y}")
    if cert.manufacturer:
        parts.append(f"— {cert.manufacturer.short_name or cert.manufacturer.full_name}")
    return ' '.join(parts)


def _protocols_formfield(db_field, **kwargs):
    """Протоколы испытаний — список галочек с понятными подписями вместо тесного
    выпадающего списка, где виден только номер документа."""
    kwargs['widget'] = forms.CheckboxSelectMultiple()
    formfield = db_field.formfield(**kwargs)
    if formfield is None:
        return None
    formfield.label = 'Протоколы испытаний'
    formfield.required = False
    formfield.label_from_instance = _protocol_label
    formfield.queryset = formfield.queryset.order_by('-issue_date', 'document_number')
    if formfield.queryset.exists():
        formfield.help_text = 'Попадут в реестр к АОСР. Обычно можно оставить пустым.'
    else:
        formfield.help_text = ('Протоколов испытаний в справочнике пока нет. Добавьте документ '
                               'с типом «Протокол испытаний» в разделе «Сертификаты».')
    return formfield


def _related_create_url(model):
    if model.__name__ == 'HiddenWorkAct':
        return reverse('act_create')

    # Позиции поставки создаются строками внутри накладной.
    slug = 'deliveries' if model.__name__ == 'DeliveryItem' else None
    if slug is None:
        from .registry import get_entries
        slug = next((
            entry['slug']
            for _, entries in get_entries()
            for entry in entries
            if entry['model'] is model
        ), None)
    return reverse('ref_create', args=[slug]) if slug else None


def _formfield_callback(db_field, **kwargs):
    # Протоколы испытаний у акта — отдельный виджет вместо стандартного множественного списка
    if db_field.model._meta.label_lower == 'core.hiddenworkact' and db_field.name == 'protocols':
        return _protocols_formfield(db_field, **kwargs)
    if isinstance(db_field, models.DateField) and not isinstance(db_field, models.DateTimeField):
        # текстовое поле вместо type=date: дату можно писать и вставлять вручную
        # (нормализация на клиенте — base.html, .date-input), принимаем оба формата
        kwargs['widget'] = forms.DateInput(
            attrs={'type': 'text', 'class': 'date-input', 'placeholder': 'дд.мм.гггг',
                   'inputmode': 'numeric', 'autocomplete': 'off'},
            format='%d.%m.%Y')
        kwargs['input_formats'] = ['%d.%m.%Y', '%d.%m.%y', '%Y-%m-%d']
    if isinstance(db_field, models.TextField) and not db_field.choices:
        kwargs['widget'] = forms.Textarea(attrs={'rows': 3})
    if isinstance(db_field, models.ForeignKey):
        kwargs.setdefault('empty_label', '—')
    formfield = db_field.formfield(**kwargs)
    if formfield is None:
        return None

    formfield.label = field_label(db_field)

    # пустой вариант в списках выбора — прочерк вместо «- Select an option -»
    if db_field.choices and hasattr(formfield, 'choices'):
        formfield.choices = [('', '—') if value == '' else (value, label) for value, label in formfield.choices]

    if isinstance(db_field, models.ForeignKey):
        formfield.create_url = _related_create_url(db_field.related_model)
        formfield.create_label = str(db_field.related_model._meta.verbose_name)
        related_fields = {f.name for f in db_field.related_model._meta.fields}
        if 'category' in related_fields and db_field.related_model.__name__ == 'Material':
            group_choices(formfield, lambda m: m.category)
        if 'document_type' in related_fields and any(
                isinstance(f, models.FileField) for f in db_field.related_model._meta.fields):
            formfield.label_from_instance = _document_label
        # в списках выбора показываем то, по чему запись можно узнать, а не только номер
        related_name = db_field.related_model.__name__
        if related_name == 'Person':
            formfield.queryset = formfield.queryset.select_related('organization')
            formfield.label_from_instance = lambda p: p.display_name
        elif related_name == 'DocumentationSection':
            formfield.queryset = formfield.queryset.select_related('site')
            formfield.label_from_instance = _section_label
        elif related_name == 'HiddenWorkAct':
            formfield.queryset = formfield.queryset.select_related('section__site')
            formfield.label_from_instance = _act_label
        elif related_name == 'EstimateLine':
            formfield.queryset = formfield.queryset.select_related('room__site', 'work_type')
            formfield.label_from_instance = _estimate_line_label
        elif related_name == 'DeliveryItem':
            formfield.queryset = formfield.queryset.select_related('delivery_note', 'material')
            formfield.label_from_instance = _delivery_item_label

        # Длинные справочники получают поиск на клиенте (components.js).
        # Маленькие списки остаются нативными — это решает JS по числу вариантов.
        formfield.widget.attrs.setdefault('data-autocomplete', 'fk')
        formfield.widget.attrs.setdefault('data-model', db_field.related_model._meta.model_name)
        if formfield.create_url:
            formfield.widget.attrs.setdefault('data-create-url', formfield.create_url)

    return formfield


class BaseForm(forms.ModelForm):
    """Если у модели есть поле file_hash и файл — считает хеш и не даёт загрузить дубль."""

    def clean(self):
        cleaned = super().clean()
        model = self._meta.model
        field_names = {f.name for f in model._meta.get_fields()}
        if 'file_hash' not in field_names:
            return cleaned

        for f in model._meta.get_fields():
            if isinstance(f, models.FileField):
                uploaded = cleaned.get(f.name)
                if uploaded and hasattr(uploaded, 'chunks') and f.name in self.changed_data:
                    sha = hashlib.sha256()
                    for chunk in uploaded.chunks():
                        sha.update(chunk)
                    uploaded.seek(0)
                    digest = sha.hexdigest()
                    duplicate = model.objects.filter(file_hash=digest).exclude(pk=self.instance.pk).first()
                    if duplicate:
                        raise forms.ValidationError(f'Этот файл уже загружен: «{duplicate}» (запись №{duplicate.pk}).')
                    self.instance.file_hash = digest
        return cleaned


def make_form(model, exclude=()):
    exclude = list(exclude) + [f.name for f in model._meta.get_fields() if f.name == 'file_hash']
    return modelform_factory(
        model, form=BaseForm, exclude=exclude, formfield_callback=_formfield_callback,
    )


def make_inline_formset(parent_model, child_model, fk_name, exclude=(), extra=2):
    return inlineformset_factory(
        parent_model, child_model, form=BaseForm, fk_name=fk_name,
        exclude=list(exclude), extra=extra, can_delete=True,
        formfield_callback=_formfield_callback,
    )
