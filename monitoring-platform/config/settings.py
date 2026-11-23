"""Django settings for the Employee Monitoring platform.

Secrets and environment-specific values come from environment variables,
never from source code.
"""
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_env_file(path):
    """Read KEY=VALUE lines from a local .env file into the environment.

    A variable already set in the real environment wins over the file. Tests skip the file
    (DJANGO_SKIP_ENV_FILE=1, or "manage.py test"), so a developer's local settings cannot change their results.
    """
    if os.environ.get("DJANGO_SKIP_ENV_FILE") == "1" or sys.argv[1:2] == ["test"] or not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file(BASE_DIR / ".env")


def _runserver_is_local():
    """True when "manage.py runserver" listens only on this computer (the default address is 127.0.0.1)."""
    address = next((arg for arg in sys.argv[2:] if not arg.startswith("-")), "")
    if ":" in address:
        host = address.rsplit(":", 1)[0]
    elif address.isdigit() or address == "":
        host = ""  # only a port, or nothing: the default address
    else:
        host = address
    return host in ("", "127.0.0.1", "localhost")


# True only for "manage.py test". Looking at the first argument means a process that merely
# has the word "test" somewhere in its arguments cannot switch on the development settings.
RUNNING_TESTS = sys.argv[1:2] == ["test"]
# True for "manage.py runserver" on this computer. Django's development server is not a production
# server, so a local runserver starts in development mode without extra setup. Any other address,
# or any other server, still needs DJANGO_DEBUG=1 or a secret key.
RUNNING_LOCAL_SERVER = sys.argv[1:2] == ["runserver"] and _runserver_is_local()
# Debug is off unless it is switched on explicitly (DJANGO_DEBUG=1) or by the local runserver.
DEBUG = os.environ.get("DJANGO_DEBUG") == "1" or RUNNING_LOCAL_SERVER
# Insecure development defaults are allowed only in debug mode and in tests.
DEV_MODE = DEBUG or RUNNING_TESTS

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if DEV_MODE:
        SECRET_KEY = "dev-only-insecure-key-not-for-production"
    else:
        raise ImproperlyConfigured("Set the DJANGO_SECRET_KEY environment variable.")

ALLOWED_HOSTS = [
    host
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host
]
# Render sets these for the public address of the web service (empty anywhere else).
if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])
CSRF_TRUSTED_ORIGINS = [os.environ["RENDER_EXTERNAL_URL"]] if os.environ.get("RENDER_EXTERNAL_URL") else []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    # Project apps (TAD section 3).
    "organizations",
    "accounts",
    "access_control",
    "audit",
    "workforce",
    "devices",
    "monitoring",
    "productivity",
    "analytics",
    "screenshots",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves the collected static files when DEBUG is off (needed on Render). Must sit right after SecurityMiddleware.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.MembershipMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database: PostgreSQL (TAD section 2). SQLite is a quick local fallback only.
# Render provides DATABASE_URL (postgres://user:password@host:port/name). Locally, select PostgreSQL
# with DB_ENGINE=postgres (see monitoring-platform/README.md).
if os.environ.get("DATABASE_URL"):
    _url = urlparse(os.environ["DATABASE_URL"])
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _url.path.lstrip("/"),
            "USER": unquote(_url.username or ""),
            "PASSWORD": unquote(_url.password or ""),
            "HOST": _url.hostname or "",
            "PORT": str(_url.port or 5432),
            # Connections to a hosted database use TLS. Local development does not.
            "OPTIONS": {} if DEV_MODE else {"sslmode": "require"},
            # Reuse each database connection for up to a minute instead of opening a new one per request.
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
        }
    }
elif os.environ.get("DB_ENGINE", "sqlite") == "postgres":
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "dev_password" if DEV_MODE else "")
    if not DB_PASSWORD:
        raise ImproperlyConfigured("Set the DB_PASSWORD environment variable.")
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "employee_monitoring"),
            "USER": os.environ.get("DB_USER", "monitoring"),
            "PASSWORD": DB_PASSWORD,
            "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DB_PORT", "5433"),  # matches docker-compose.yml
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            # SQLITE_PATH lets demo data live in its own file (for example demo.sqlite3).
            "NAME": BASE_DIR / os.environ.get("SQLITE_PATH", "db.sqlite3"),
            # A page of screenshot thumbnails writes one audit row per image at once. Take the write lock
            # up front and wait for it, instead of failing with "database is locked". Development only.
            "OPTIONS": {"timeout": 30, "transaction_mode": "IMMEDIATE"},
        }
    }

AUTH_USER_MODEL = "accounts.User"
# The same email can have a separate account in each organization, so sign-in uses our own backend.
AUTHENTICATION_BACKENDS = ["accounts.backends.OrganizationBackend"]
# Django warns that "email" is not unique. It is unique per organization (and for the Super Admin), as the backend expects.
SILENCED_SYSTEM_CHECKS = ["auth.W004"]
LOGIN_URL = "login"
# After sign-in, "dashboard" sends each role to its own dashboard.
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"
# One-time links (invitations and password resets) expire after 72 hours.
PASSWORD_RESET_TIMEOUT = 60 * 60 * 72

# Email (password reset, invitations and activation codes). On Render Free, which blocks SMTP, set BREVO_API_KEY:
# mail is then sent over Brevo's HTTPS API (config/brevo_email.py) and the SMTP settings below are not used.
# Without BREVO_API_KEY, mail goes over SMTP (Gmail for local development, or any SMTP server). With neither
# BREVO_API_KEY nor EMAIL_HOST, sending fails and the app shows that the email could not be sent
# (see accounts/checks.py for the startup warning).
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
EMAIL_BACKEND = "config.brevo_email.BrevoEmailBackend" if BREVO_API_KEY else "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
# Stop waiting on a mail server after this many seconds, so a blocked SMTP port cannot hang a web request.
EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", "10"))
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "1") == "1"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "no-reply@example.invalid")

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# A fast password hasher keeps the test suite quick. It is never used outside tests.
if RUNNING_TESTS:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
# Time zone for showing times to people (Audit logs). Stored times stay in UTC; the org policy zone is separate.
DISPLAY_TIMEZONE = os.environ.get("DISPLAY_TIMEZONE", "Asia/Kolkata")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]  # app.js and other shared files

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024  # same size limit as an event batch

# Account messages (sign-in, password reset) go to the server terminal, so a skipped or sent email is visible.
# They never include an email address.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {
        "accounts": {"handlers": ["console"], "level": "INFO", "propagate": False},
        # Only real server errors (5xx); a 403 or 404 is normal and would only fill the log.
        "django.request": {"level": "ERROR"},
    },
    # Everything else (including server errors from django.request) goes to the console at WARNING and above, so
    # Render's log shows the traceback of a 500 error. Without this, Django prints it only when DEBUG is on.
    "root": {"handlers": ["console"], "level": "WARNING"},
}

# Agent API (docs/api-contract.yaml). Each API view lists its own authentication.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "devices.api_errors.agent_exception_handler",
    # Per device, except registration (per IP address). Defaults to confirm. An agent catching up on
    # a full queue of 10,000 events sends 20 batches, well inside the events limit.
    "DEFAULT_THROTTLE_RATES": {
        "agent_register": "10/hour",
        "agent_events": "60/minute",
        "agent_calls": "30/minute",
        "agent_pairing_poll": "600/hour",
    },
}

# Tests never talk to S3 (or to the local stand-in). They use an in-memory fake.
# Screenshot storage: a private S3 bucket (TAD section 7). In development, scripts/dev_s3.py provides a
# local stand-in on https://127.0.0.1:9100. In production leave S3_ENDPOINT_URL empty (real Amazon S3),
# and leave the access keys empty so the server's own role is used. Secrets come from environment variables.
# For now, development keeps images on this computer's disk instead (SCREENSHOT_STORAGE=screenshots.storage.S3Storage
# switches back to the S3 stand-in). LocalStorage refuses to start outside development.
SCREENSHOT_STORAGE = os.environ.get(
    "SCREENSHOT_STORAGE",
    "screenshots.tests.fakes.FakeStorage" if RUNNING_TESTS
    else "screenshots.storage.LocalStorage" if DEV_MODE
    else "screenshots.storage.S3Storage",
)
SCREENSHOT_LOCAL_DIR = os.environ.get("SCREENSHOT_LOCAL_DIR", str(BASE_DIR / "screenshot_data"))
S3_BUCKET = os.environ.get("S3_BUCKET", "employee-monitoring-screenshots")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "https://127.0.0.1:9100" if DEV_MODE else "")
_DEMO_CERT = BASE_DIR / "certs" / "cert.pem"
S3_CA_BUNDLE = os.environ.get("S3_CA_BUNDLE", str(_DEMO_CERT) if DEV_MODE and _DEMO_CERT.exists() else "")
S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID", "dev" if DEV_MODE else "")
S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY", "dev-secret" if DEV_MODE else "")
# How long the signed links last (seconds). Defaults to confirm.
SCREENSHOT_UPLOAD_LINK_SECONDS = 300
SCREENSHOT_VIEW_LINK_SECONDS = 60
# An upload that was authorized but never finished is given up after this long.
SCREENSHOT_UPLOAD_GIVE_UP_SECONDS = 3600

# How long a device credential and a registration code stay valid (days).
# Both values are defaults to confirm.
AGENT_CREDENTIAL_DAYS = 30
REGISTRATION_CODE_DAYS = 7
# A device counts as online if its last heartbeat is newer than this (seconds).
DEVICE_ONLINE_SECONDS = 15 * 60
# The agent's settings, shown to the Admin on the registration code page. The API address the agent
# uses, and the certificate file it trusts. In development these point at the local HTTPS server
# (scripts/run_https.ps1). Production must set AGENT_BASE_URL to the real HTTPS address.
AGENT_BASE_URL = os.environ.get("AGENT_BASE_URL", "https://127.0.0.1:8443/api/agent/v1" if DEV_MODE else "")
# The agent trusts the local development CA (rootCA.pem), which signs the server certificate.
AGENT_CA_BUNDLE = os.environ.get("AGENT_CA_BUNDLE", "monitoring-platform/certs/rootCA.pem" if DEV_MODE else "")

# The website's own address, used to send an employee's browser to their dashboard (never the API address above,
# which can be a local HTTPS server the browser does not trust). Render sets RENDER_EXTERNAL_URL itself.
SITE_URL = (
    os.environ.get("SITE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or ("http://127.0.0.1:8000" if DEV_MODE else "")
).rstrip("/")

# HTTPS everywhere outside development (TAD section 11).
if not DEV_MODE:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    # Render ends HTTPS at its proxy and passes the original scheme in this header.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Starting monitoring from the dashboard (PIN). A start waits this long for the agent to confirm (seconds).
MONITORING_START_TIMEOUT_SECONDS = 10 * 60
# How long an Admin's request waits for the employee to approve it on their dashboard.
MONITORING_APPROVAL_TIMEOUT_SECONDS = 24 * 60 * 60
# Once the agent confirms, it may collect for this long, then it stops until the employee starts again (seconds).
# Both values are defaults to confirm.
MONITORING_AUTHORIZED_SECONDS = 8 * 60 * 60

# Employee pairing (docs/employee-pairing-plan.md). Both values are defaults to confirm.
SETUP_WINDOW_MINUTES = 30  # how long an Admin's "Start Monitoring Setup" lets the employee approve a PC
PAIRING_REQUEST_MINUTES = 10  # how long a PC's pairing request waits for approval

# The built installer (build.ps1 copies it here). Served only to logged-in users, never as a public static file. Unsigned unless build.ps1 signed it.
AGENT_INSTALLER_PATH = os.environ.get("AGENT_INSTALLER_PATH", str(BASE_DIR / "agent_dist" / "WinAgentSetup.exe"))
