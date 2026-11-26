from django.urls import path

from . import views

urlpatterns = [
    path("", views.organization_list, name="organization_list"),
    path("new/", views.organization_create, name="organization_create"),
    path("<int:pk>/", views.organization_detail, name="organization_detail"),
    path("<int:pk>/figures/", views.organization_figures_panel, name="organization_figures"),
    path("<int:pk>/active/", views.organization_set_active, name="organization_set_active"),
    path("<int:pk>/admins/new/", views.organization_admin_create, name="organization_admin_create"),
    path("admins/", views.admin_list, name="admin_list"),
    path("admins/<int:pk>/resend-invite/", views.organization_admin_resend_invite, name="organization_admin_resend_invite"),
    path("admins/<int:pk>/active/", views.organization_admin_set_active, name="organization_admin_set_active"),
    path("admins/<int:pk>/edit/", views.organization_admin_edit, name="organization_admin_edit"),
    path("admins/<int:pk>/delete/", views.organization_admin_delete, name="organization_admin_delete"),
]
