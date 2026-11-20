from django.urls import path

from . import api_views

urlpatterns = [
    path("devices/register/", api_views.RegisterView.as_view(), name="agent_register"),
    path("config/", api_views.ConfigView.as_view(), name="agent_config"),
    path("events/batch/", api_views.EventBatchView.as_view(), name="agent_events"),
    path("screenshots/authorize/", api_views.ScreenshotAuthorizeView.as_view(), name="agent_screenshot_authorize"),
    path(
        "screenshots/<uuid:screenshot_id>/complete/",
        api_views.ScreenshotCompleteView.as_view(),
        name="agent_screenshot_complete",
    ),
    path("devices/credential/renew/", api_views.CredentialRenewView.as_view(), name="agent_credential_renew"),
    path("heartbeat/", api_views.HeartbeatView.as_view(), name="agent_heartbeat"),
    path("pairing/requests/", api_views.PairingRequestView.as_view(), name="agent_pairing_request"),
    path("pairing/requests/<uuid:request_id>/", api_views.PairingPollView.as_view(), name="agent_pairing_poll"),
]
