from django.urls import path

from . import views

urlpatterns = [
    path("settings/", views.policy_settings, name="policy_settings"),
]
