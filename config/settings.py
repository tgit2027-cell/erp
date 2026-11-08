import os
import secrets
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

APP_ENV = os.environ.get("APP_ENV", "development")
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = os.environ.get("SESSION_SECRET")
if not SECRET_KEY:
    if APP_ENV != "demo":
        raise RuntimeError("Set DJANGO_SECRET_KEY outside the explicitly configured demo environment.")
    SECRET_KEY = secrets.token_urlsafe(48)

# Debug pages expose settings and stack traces, so they stay off unless a
# developer opts in with DJANGO_DEBUG=true for a private local run.
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = [host.strip() for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0").split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "foundation",
    "audit",
    "chat",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "audit.middleware.AuditMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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
ASGI_APPLICATION = "config.asgi.application"

DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    parsed_database_url = urlparse(DATABASE_URL)
    if parsed_database_url.scheme not in {"postgres", "postgresql"}:
        raise RuntimeError("DATABASE_URL must use the postgres:// or postgresql:// scheme.")
    database_options = {
        key: values[-1]
        for key, values in parse_qs(parsed_database_url.query).items()
    }
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed_database_url.path.lstrip("/")),
            "USER": unquote(parsed_database_url.username or ""),
            "PASSWORD": unquote(parsed_database_url.password or ""),
            "HOST": parsed_database_url.hostname or "",
            "PORT": str(parsed_database_url.port or "5432"),
            "OPTIONS": database_options,
            "CONN_MAX_AGE": 0,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "tivoli_erp_demo"),
            "USER": os.environ.get("DB_USER", os.environ.get("USER", "postgres")),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
            "PORT": os.environ.get("DB_PORT", "55432"),
            "CONN_MAX_AGE": 0,
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en"
TIME_ZONE = "Asia/Qatar"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = Path(os.environ.get("DJANGO_STATIC_ROOT") or (BASE_DIR / "staticfiles")).resolve()
MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.environ.get("DJANGO_MEDIA_ROOT") or (BASE_DIR / "media")).resolve()
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = int(os.environ.get("DJANGO_SESSION_COOKIE_AGE", str(12 * 60 * 60)))
# Failed sign-ins are counted per username in the default cache. The local
# memory cache is per process; a shared cache is needed once more than one
# application process serves logins.
LOGIN_FAILURE_LIMIT = 10
LOGIN_LOCKOUT_SECONDS = 15 * 60
# The audit log records REMOTE_ADDR. Behind exactly one reverse proxy that
# appends to X-Forwarded-For, set this to true so the log keeps the visitor's
# address instead of the proxy's. Only the last entry, the one the proxy added,
# is used. Leave it false unless the app is reachable only through that proxy.
AUDIT_TRUST_X_FORWARDED_FOR = os.environ.get("AUDIT_TRUST_X_FORWARDED_FOR", "false").lower() == "true"
SESSION_COOKIE_SAMESITE = "Lax"
COOKIE_SECURE = os.environ.get("DJANGO_COOKIE_SECURE", "false" if DEBUG else "true").lower() == "true"
SESSION_COOKIE_SECURE = COOKIE_SECURE
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = COOKIE_SECURE
# Keep full origins (scheme, hostname, and port) in this environment setting.
# The demo runner sets only the current preview origin; production config is supplied separately.
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "foundation.renderers.LoadMeasuredJSONRenderer",
    ],
}

# Email alerts (approvals waiting, documents about to expire). Without SMTP settings, emails are
# written to the server log instead of being sent.
EMAIL_BACKEND = os.environ.get("ERP_EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.environ.get("ERP_EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.environ.get("ERP_EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("ERP_EMAIL_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("ERP_EMAIL_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("ERP_EMAIL_TLS", "true").lower() == "true"
DEFAULT_FROM_EMAIL = os.environ.get("ERP_EMAIL_FROM", "Tivoli ERP <erp-alerts@example.invalid>")
ERP_BASE_URL = os.environ.get("ERP_BASE_URL", "http://localhost:5173").rstrip("/")

# The HR Portal for all staff (Nov 2026) is switched on: the owner said go for the live test (6 Nov 2026).
# Set ERP_HR_PORTAL=off to hide it again; while it is off the menu hides it and every portal address answers "not found".
HR_PORTAL_ENABLED = os.environ.get("ERP_HR_PORTAL", "on").strip().lower() == "on"
