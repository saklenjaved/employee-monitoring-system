from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("account/", views.profile, name="profile"),
    path("account/password/", views.password_change, name="password_change"),
    path("login/", views.LimitedLoginView.as_view(), name="login"),
    path("activate/", views.activate, name="activate"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("password-reset/", views.LimitedPasswordResetView.as_view(), name="password_reset"),
    path("password-reset/sent/", auth_views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path(
        "password-reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path("password-reset/complete/", auth_views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
]
