from django.urls import path

from . import views

urlpatterns = [
    path("rules/", views.rule_list, name="rule_list"),
    path("rules/<int:pk>/category/", views.rule_change, name="rule_change"),
    path("rules/<int:pk>/delete/", views.rule_delete, name="rule_delete"),
]
