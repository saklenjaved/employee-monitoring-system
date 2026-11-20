from django.urls import path

from . import views

urlpatterns = [
    path("people/<int:user_id>/registration-codes/new/", views.registration_code_create, name="registration_code_create"),
    path("people/<int:user_id>/monitoring-setup/start/", views.monitoring_setup_start, name="monitoring_setup_start"),
    path("people/<int:user_id>/monitoring/start/", views.admin_monitoring_start, name="admin_monitoring_start"),
    path("deployment/", views.deployment, name="deployment"),
    path("deployment/token/", views.deployment_token_new, name="deployment_token_new"),
    path("deployment/script/", views.deployment_script, name="deployment_script"),
    path("deployment/installer/", views.deployment_installer, name="deployment_installer"),
    path("me/install-package/", views.install_package, name="install_package"),
    path("me/monitoring/pin/", views.monitoring_pin_set, name="monitoring_pin_set"),
    path("me/monitoring/start/", views.monitoring_start, name="monitoring_start"),
    path("me/pairing/<uuid:request_id>/approve/", views.pairing_approve, name="pairing_approve"),
    path("<uuid:pk>/revoke/", views.device_revoke, name="device_revoke"),
]
