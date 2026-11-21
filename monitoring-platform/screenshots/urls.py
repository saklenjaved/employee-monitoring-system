from django.urls import path

from . import views

urlpatterns = [
    path("", views.screenshot_list, name="screenshot_list"),
    path("settings/", views.settings_page, name="screenshot_settings"),
    path("exclusions/", views.exclusions_page, name="screenshot_exclusions"),
    path("exclusions/<int:pk>/remove/", views.exclusion_remove, name="screenshot_exclusion_remove"),
    path("<int:pk>/", views.screenshot_detail, name="screenshot_detail"),
    path("<int:pk>/image/", views.screenshot_image, name="screenshot_image"),
    path("<int:pk>/download/", views.screenshot_download, name="screenshot_download"),
    path("<int:pk>/delete/", views.screenshot_delete, name="screenshot_delete"),
]
