from django.urls import path

from . import local_views

urlpatterns = [
    path("upload/<str:token>/", local_views.upload, name="local_storage_upload"),
    path("file/<str:token>/", local_views.download, name="local_storage_file"),
]
