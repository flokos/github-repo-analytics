"""Django settings.

Every deployment-specific value comes from the environment; nothing secret is hard-coded.
The defaults suit the local Docker Compose stack and are not production settings.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name: str, default: str = "", *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and not value:
        raise ImproperlyConfigured(f"The {name} environment variable is required.")
    return value


def env_bool(name: str, default: bool = False) -> bool:
    return env(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


# --- Core -----------------------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

# The API has no user accounts, sessions, templates or admin site, so the usual contrib apps are
# deliberately absent.
INSTALLED_APPS = [
    "corsheaders",
    "rest_framework",
    "analytics",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# --- Database -------------------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": env("DATABASE_HOST", "postgres"),
        "PORT": env("DATABASE_PORT", "5432"),
        "NAME": env("DATABASE_NAME", "analytics"),
        "USER": env("DATABASE_USER", "analytics"),
        "PASSWORD": env("DATABASE_PASSWORD", required=True),
        "CONN_MAX_AGE": 60,
        "CONN_HEALTH_CHECKS": True,
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Internationalisation -------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

# --- HTTP API -------------------------------------------------------------------------------------
# Browsers may call the API cross-origin only from these origins (the Vite dev server by default).
# The bundled dashboard is served from the same origin through nginx and does not need CORS.
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")

REST_FRAMEWORK = {
    # Public endpoints are intentionally unauthenticated (local demonstration project).
    # Internal endpoints opt in to token authentication on the view.
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "analytics.api.exception_handler.api_exception_handler",
    "DEFAULT_THROTTLE_RATES": {
        "repository-create": env("REPOSITORY_CREATE_THROTTLE", "30/min"),
    },
    # nginx sits in front of the API in the Compose stack and appends the client address.
    "NUM_PROXIES": 1,
}

# Shared secret presented by Airflow on /api/internal/ endpoints. Required: without it the
# internal API would have no protection, so Django refuses to start.
INTERNAL_API_TOKEN = env("INTERNAL_API_TOKEN", required=True)

# --- GitHub ---------------------------------------------------------------------------------------
# A fixed, trusted host. User input never influences where requests are sent.
GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"
GITHUB_TOKEN = env("GITHUB_TOKEN")
GITHUB_CONNECT_TIMEOUT_SECONDS = 3.05
GITHUB_READ_TIMEOUT_SECONDS = 8.0

# --- Logging --------------------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "analytics": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
