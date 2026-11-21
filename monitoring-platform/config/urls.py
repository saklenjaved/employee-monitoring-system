from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/agent/v1/", include("devices.api_urls")),
    path("platform/organizations/", include("organizations.urls")),
    path("workforce/", include("workforce.urls")),
    path("devices/", include("devices.urls")),
    path("monitoring/", include("monitoring.urls")),
    path("productivity/", include("productivity.urls")),
    path("analytics/", include("analytics.urls")),
    path("dashboard/", include("analytics.dashboard_urls")),
    path("screenshots/", include("screenshots.urls")),
    path("storage/", include("screenshots.local_urls")),  # development-only local image storage; 404 unless it is in use
    path("audit/", include("audit.urls")),
    path("", include("accounts.urls")),
]
