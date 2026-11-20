"""Страницы пространства объекта: список объектов, обзор и служебная заглушка."""

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render

from .models import (
    ConstructionSite, DeliveryItem, DeliveryNote, DocumentationSection,
    HiddenWorkAct, WorkVolume,
)
from .site_catalogs import certificates_for_site, materials_for_site
from .site_context import get_current_site, set_current_site


@login_required
def home(request):
    """Корень ведёт в выбранный объект, а если он не выбран — к списку объектов."""
    site = get_current_site(request)
    if site:
        return redirect('site_overview', pk=site.pk)
    return redirect('site_list')


@login_required
def site_list(request):
    sites = ConstructionSite.objects.select_related(
        'customer', 'general_contractor', 'designer',
    ).annotate(
        acts_count=Count('documentationsection__hiddenworkact', distinct=True),
        deliveries_count=Count('deliverynote', distinct=True),
    ).order_by('name')
    q = request.GET.get('q', '').strip()
    if q:
        sites = sites.filter(
            Q(name__icontains=q) | Q(address__icontains=q)
            | Q(customer__short_name__icontains=q)
            | Q(general_contractor__short_name__icontains=q)
        ).distinct()
    return render(request, 'core/site_list.html', {'sites': sites, 'q': q})


@login_required
def site_select(request):
    """Переключатель в шапке: сохраняет объект в session и открывает его обзор."""
    if request.method != 'POST':
        return redirect('site_list')
    pk = request.POST.get('site')
    if not pk:
        set_current_site(request, None)
        return redirect('site_list')
    site = get_object_or_404(ConstructionSite, pk=pk)
    set_current_site(request, site)
    return redirect('site_overview', pk=site.pk)


@login_required
def site_overview(request, pk):
    site = get_object_or_404(ConstructionSite.objects.select_related(
        'customer', 'general_contractor', 'designer',
    ), pk=pk)
    set_current_site(request, site)

    acts = HiddenWorkAct.objects.filter(section__site=site)
    counts = {
        row['status']: row['n']
        for row in acts.values('status').annotate(n=Count('pk'))
    }
    status_counts = [
        {'value': value, 'label': label, 'count': counts.get(value, 0)}
        for value, label in HiddenWorkAct._meta.get_field('status').choices
    ]
    free_volumes = WorkVolume.objects.filter(act__isnull=True).filter(
        Q(room__site=site) | Q(work_type__section__site=site)
    ).distinct().count()
    unmatched_items = DeliveryItem.objects.filter(
        delivery_note__site=site, material__isnull=True,
    ).count()

    return render(request, 'core/site_overview.html', {
        'site': site,
        'status_counts': status_counts,
        'acts_total': sum(c['count'] for c in status_counts),
        'sections_total': DocumentationSection.objects.filter(site=site).count(),
        'deliveries_total': DeliveryNote.objects.filter(site=site).count(),
        'certificates_total': certificates_for_site(site).count(),
        'materials_total': materials_for_site(site).count(),
        'free_volumes': free_volumes,
        'unmatched_items': unmatched_items,
        'locked_site': site,
    })


@login_required
def site_certificates(request, site_pk):
    """Сертификаты объекта: материалы, поставлявшиеся сюда, и документы из загрузок объекта."""
    site = get_object_or_404(ConstructionSite, pk=site_pk)
    set_current_site(request, site)
    certificates = certificates_for_site(site).select_related('manufacturer').prefetch_related(
        'materialcertificate_set__material').order_by('-issue_date', '-pk')
    q = request.GET.get('q', '').strip()
    if q:
        certificates = certificates.filter(
            Q(document_number__icontains=q) | Q(product_text__icontains=q)
            | Q(manufacturer__short_name__icontains=q)
            | Q(materialcertificate__material__name__icontains=q)
        ).distinct()
    page = Paginator(certificates, 50).get_page(request.GET.get('page'))
    return render(request, 'core/site_certificates.html', {
        'site': site, 'locked_site': site, 'page': page, 'q': q,
    })


@login_required
def site_materials(request, site_pk):
    """Материалы из накладных объекта. Справочник материалов остаётся глобальным."""
    site = get_object_or_404(ConstructionSite, pk=site_pk)
    set_current_site(request, site)
    materials = materials_for_site(site).select_related('manufacturer', 'standard')
    q = request.GET.get('q', '').strip()
    if q:
        materials = materials.filter(
            Q(name__icontains=q) | Q(category__icontains=q)
            | Q(manufacturer__short_name__icontains=q)
            | Q(standard__designation__icontains=q)
        )
    page = Paginator(materials, 50).get_page(request.GET.get('page'))
    return render(request, 'core/site_materials.html', {
        'site': site, 'locked_site': site, 'page': page, 'q': q,
    })
