import logging

from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import PasswordChangeForm
from django.core.cache import cache
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render

from access_control.permissions import role_required

from . import services
from .forms import ActivationForm, OrganizationLoginForm, ProfileForm
from .ratelimit import client_ip, hit, key_for, too_many

SIGN_IN_BACKEND = "accounts.backends.OrganizationBackend"
LOGIN_FAILURES_PER_EMAIL = 10
LOGIN_FAILURES_PER_IP = 30
LOGIN_WINDOW_SECONDS = 15 * 60
RESET_REQUESTS_PER_IP = 10
RESET_REQUESTS_PER_EMAIL = 3
RESET_WINDOW_SECONDS = 60 * 60

logger = logging.getLogger(__name__)


def _too_many_attempts(request):
    return render(request, "registration/too_many_attempts.html", status=429)


def activate(request):
    """Activation page for invited people. Anyone may open it; the code sent by email is the proof."""
    # The link from the invitation email carries a signed ID; it pre-fills the email field. The field stays editable.
    invited = services.invited_email(request.GET.get("invite"))
    form = ActivationForm(request.POST or None, initial={"email": invited})
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        user, error = services.activate_membership(request, data["email"], data["code"], data["password"])
        if error:
            form.add_error(None, error)
        elif data["password"] and user.check_password(data["password"]):
            # The password is the one the person just set, or their own existing one. Only then is the
            # account signed in, and the dashboard for its role (Admin or Employee) is shown.
            login(request, user, backend=SIGN_IN_BACKEND)
            messages.success(request, "Your account is active.")
            return redirect("dashboard")
        else:
            messages.success(request, "Your account is active. Sign in with your email and password.")
            return redirect("login")
    return render(request, "accounts/activate.html", {"form": form, "public_page": True})


@role_required()
def home(request):
    """The site's root address goes straight to the signed-in person's dashboard for their role."""
    return redirect("dashboard")


@role_required()
def profile(request):
    """The signed-in person's own name. Email, role and organization are set by an Admin, not here."""
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        services.change_own_name(request.user, form)
        messages.success(request, "Profile saved.")
        return redirect("profile")
    return render(request, "accounts/profile.html", {"form": form})


@role_required()
def password_change(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        services.change_own_password(request.user, form)
        update_session_auth_hash(request, request.user)  # this session stays signed in; the others are signed out
        messages.success(request, "Password changed. Your other sessions have been signed out.")
        return redirect("password_change")
    return render(request, "accounts/password_change.html", {"form": form})


class LimitedLoginView(auth_views.LoginView):
    """Login that slows down password guessing, by email address and by IP address."""

    authentication_form = OrganizationLoginForm
    redirect_authenticated_user = True

    def _keys(self, request):
        email = request.POST.get("username", "")
        return key_for("login-fail-email", email), f"login-fail-ip:{client_ip(request)}"

    def post(self, request, *args, **kwargs):
        email_key, ip_key = self._keys(request)
        if too_many(email_key, LOGIN_FAILURES_PER_EMAIL) or too_many(ip_key, LOGIN_FAILURES_PER_IP):
            return _too_many_attempts(request)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        # Being asked to choose an organization is not a failed attempt: the password was right.
        if not form.needs_organization:
            email_key, ip_key = self._keys(self.request)
            hit(email_key, LOGIN_WINDOW_SECONDS)
            hit(ip_key, LOGIN_WINDOW_SECONDS)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self._keys(self.request)[0])
        return super().form_valid(form)


class LimitedPasswordResetView(auth_views.PasswordResetView):
    """Password reset that cannot be used to flood an inbox.

    Past the limit for an email address the answer stays the same ("check your
    email") but no email is sent, so nothing reveals which addresses have accounts.
    """

    def post(self, request, *args, **kwargs):
        ip_key = f"reset-ip:{client_ip(request)}"
        if too_many(ip_key, RESET_REQUESTS_PER_IP):
            return _too_many_attempts(request)
        hit(ip_key, RESET_WINDOW_SECONDS)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        email_key = key_for("reset-email", form.cleaned_data["email"])
        if too_many(email_key, RESET_REQUESTS_PER_EMAIL):
            # The visitor still sees "check your email". The server log says why no email was sent.
            logger.warning("password reset skipped: %s requests for one address in an hour", RESET_REQUESTS_PER_EMAIL)
            return HttpResponseRedirect(self.get_success_url())
        hit(email_key, RESET_WINDOW_SECONDS)
        try:
            response = super().form_valid(form)
        except Exception as error:
            # A mail server problem must not become an error page. The answer stays the same, so nothing
            # reveals whether the address has an account. Only the error type is logged, never the address.
            logger.warning("password reset email failed (%s)", type(error).__name__)
            return HttpResponseRedirect(self.get_success_url())
        logger.info("password reset email sent")
        return response
