from django.urls import path

from . import views

urlpatterns = [
    path("unclassified/", views.unclassified_list, name="unclassified_list"),
    path("unclassified/classify/", views.unclassified_classify, name="unclassified_classify"),
]
