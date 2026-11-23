"""Startup check: invitation emails cannot be sent without a mail server and a real sender address.

Django runs this check on runserver, migrate and "check --deploy". In development a missing mail
server is only a warning, so the app still starts; in other environments it is an error.
"""
from django.conf import settings
from django.core.checks import Error, Warning, register


@register()
def invitation_email_is_configured(app_configs, **kwargs):
    if (settings.EMAIL_HOST or settings.BREVO_API_KEY) and not settings.DEFAULT_FROM_EMAIL.endswith(".invalid"):
        return []
    hint = "Set BREVO_API_KEY (or EMAIL_HOST) and DEFAULT_FROM_EMAIL (see monitoring-platform/.env.example)."
    if settings.DEV_MODE:
        return [Warning("Invitation emails cannot be sent yet.", hint=hint, id="accounts.W001")]
    return [Error("Invitation emails cannot be sent.", hint=hint, id="accounts.E001")]
