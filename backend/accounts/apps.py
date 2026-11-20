from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        from . import checks  # noqa: F401  registers the email startup check
        from .log_redaction import install

        install()  # keeps invitation tokens out of the request logs
