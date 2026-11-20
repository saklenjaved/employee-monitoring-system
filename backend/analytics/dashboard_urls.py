from django.urls import path

from . import dashboards

urlpatterns = [
    path("", dashboards.dashboard, name="dashboard"),
    path("me/", dashboards.my_dashboard, name="dashboard_me"),
    path("employees/<int:pk>/", dashboards.employee_dashboard, name="dashboard_employee"),
    path("employees/<int:pk>/live/", dashboards.employee_live_panel, name="dashboard_employee_live"),
    path("teams/", dashboards.teams_dashboard, name="dashboard_teams"),
    path("teams/<int:pk>/", dashboards.team_dashboard, name="dashboard_team"),
    path("organization/", dashboards.organization_dashboard, name="dashboard_organization"),
    path("platform/", dashboards.platform_dashboard, name="dashboard_platform"),
    path("me/trend.json", dashboards.my_trend, name="trend_me"),
    path("employees/<int:pk>/trend.json", dashboards.employee_trend, name="trend_employee"),
    path("teams/<int:pk>/trend.json", dashboards.team_trend, name="trend_team"),
    path("organization/trend.json", dashboards.organization_trend, name="trend_organization"),
    path("organization/live/", dashboards.organization_live, name="dashboard_live"),
    path("organization/monitoring.csv", dashboards.organization_monitoring_export, name="dashboard_organization_export"),
]
