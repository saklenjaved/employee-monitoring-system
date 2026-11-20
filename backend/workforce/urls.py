from django.urls import path

from . import views

urlpatterns = [
    path("departments/", views.department_list, name="department_list"),
    path("teams/", views.team_list, name="team_list"),
    path("teams/new/", views.team_create, name="team_create"),
    path("teams/<int:pk>/", views.team_edit, name="team_edit"),
    path("people/", views.person_list, name="person_list"),
    path("people/new/", views.person_create, name="person_create"),
    path("people/<int:pk>/", views.person_detail, name="person_detail"),
    path("people/<int:pk>/active/", views.person_set_active, name="person_set_active"),
    path("people/<int:pk>/setup-status/", views.person_setup_status, name="person_setup_status"),
    path("people/<int:pk>/resend-invite/", views.person_resend_invite, name="person_resend_invite"),
]
