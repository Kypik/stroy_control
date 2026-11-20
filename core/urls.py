from django.contrib.auth import views as auth_views
from django.urls import path

from . import cert_views, site_views, views, work_views

urlpatterns = [
    # Пространство объекта
    path('', site_views.home, name='home'),
    path('sites/', site_views.site_list, name='site_list'),
    path('sites/select/', site_views.site_select, name='site_select'),
    path('sites/<int:pk>/', site_views.site_overview, name='site_overview'),
    path('sites/<int:pk>/edit/', views.site_edit, name='site_edit'),
    path('sites/<int:site_pk>/acts/', views.act_list, name='site_acts'),
    path('sites/<int:site_pk>/acts/new/', views.act_create, name='site_act_create'),
    path('sites/<int:site_pk>/acts/from-volumes/', views.site_act_from_volumes,
         name='site_act_from_volumes'),
    path('sites/<int:site_pk>/certificates/', site_views.site_certificates, name='site_certificates'),
    path('sites/<int:site_pk>/certificates/upload/', cert_views.cert_uploads,
         name='site_cert_uploads'),
    path('sites/<int:site_pk>/materials/', site_views.site_materials, name='site_materials'),
    path('sites/<int:site_pk>/deliveries/', views.ref_list, {'slug': 'deliveries'},
         name='site_deliveries'),
    path('sites/<int:site_pk>/deliveries/matching/', work_views.delivery_matching,
         name='site_delivery_matching'),
    path('sites/<int:site_pk>/journal/', work_views.journal, name='site_journal'),
    path('sites/<int:site_pk>/volumes/', work_views.volumes_compare, name='site_volumes'),
    path('sites/<int:site_pk>/attention/', work_views.attention, name='site_attention'),

    # Старые адреса и имена сохранены: views сами перенаправляют в объект или к списку объектов.
    path('acts/', views.act_list, name='act_list'),
    path('acts/new/', views.act_create, name='act_create'),
    path('acts/<int:pk>/', views.act_edit, name='act_edit'),
    path('acts/<int:pk>/options/', views.act_options, name='act_options'),
    path('acts/<int:pk>/materials/', views.act_material_picker, name='act_material_picker'),
    path('acts/<int:pk>/excel/', views.act_excel, name='act_excel'),
    path('acts/<int:pk>/final-pdf/', views.act_final_pdf, name='act_final_pdf'),
    path('acts/<int:pk>/registry/', views.act_registry, name='act_registry'),
    path('acts/<int:pk>/preview/', views.act_preview, name='act_preview'),
    path('acts/<int:pk>/preview/<str:key>/<str:name>', views.act_preview_file, name='act_preview_file'),
    path('acts/<int:pk>/corrected/', views.act_corrected, name='act_corrected'),
    path('acts/<int:pk>/corrected/file/', views.act_corrected_file, name='act_corrected_file'),
    path('acts/<int:pk>/copy/', views.act_copy, name='act_copy'),
    path('acts/<int:pk>/delete/', views.act_delete, name='act_delete'),

    path('certificates/upload/', cert_views.cert_uploads, name='cert_uploads'),
    path('certificates/upload/<int:pk>/', cert_views.cert_review, name='cert_review'),
    path('certificates/upload/<int:pk>/status/', cert_views.cert_status, name='cert_status'),
    path('certificates/upload/<int:pk>/page/<int:n>/', cert_views.cert_page, name='cert_page'),
    path('certificates/upload/<int:pk>/file/', cert_views.cert_file, name='cert_file'),
    path('certificates/upload/<int:pk>/rerun/', cert_views.cert_rerun, name='cert_rerun'),
    path('certificates/upload/<int:pk>/materials/<int:index>/', cert_views.cert_materials, name='cert_materials'),
    path('certificates/<int:pk>/registry/', cert_views.cert_registry_recheck, name='cert_registry_recheck'),

    path('attention/', work_views.attention, name='attention'),
    path('attention/all/', work_views.attention_all, name='attention_all'),
    path('deliveries/matching/', work_views.delivery_matching, name='delivery_matching'),
    path('journal/', work_views.journal, name='journal'),
    path('journal/import/', work_views.journal_import, name='journal_import'),
    path('volumes/compare/', work_views.volumes_compare, name='volumes_compare'),

    path('ref/', views.ref_index, name='ref_index'),
    path('ref/<slug:slug>/', views.ref_list, name='ref_list'),
    path('ref/<slug:slug>/new/', views.ref_edit, name='ref_create'),
    path('ref/<slug:slug>/<int:pk>/', views.ref_edit, name='ref_edit'),
    path('ref/<slug:slug>/<int:pk>/delete/', views.ref_delete, name='ref_delete'),

    path('login/', auth_views.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
]
