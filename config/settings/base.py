"""Configuracion base compartida por todos los entornos."""

from datetime import timedelta
from importlib.util import find_spec
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])

# --- Aplicaciones -----------------------------------------------------------
# Monolito modular: cada carpeta de `modules/` es una app Django. Las
# dependencias permitidas entre modulos se validan con import-linter.
DJANGO_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
]
THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "corsheaders",
]
LOCAL_APPS = [
    "modules.core",
    "modules.accounts",
    "modules.audit",
    "modules.notifications",
    "modules.clients",
    "modules.content",
    "modules.publishing",
    "modules.metrics",
    "modules.legal",
]

# drf-spectacular necesita PyYAML; se activa solo si esta instalado.
HAS_SPECTACULAR = find_spec("drf_spectacular") is not None and find_spec("yaml") is not None
if HAS_SPECTACULAR:
    THIRD_PARTY_APPS.append("drf_spectacular")

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": ["django.template.context_processors.request"]},
    }
]

# --- Base de datos ----------------------------------------------------------
DATABASES = {"default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}
DATABASES["default"]["CONN_MAX_AGE"] = env.int("DB_CONN_MAX_AGE", default=60)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"

# --- Sesion (RNF01) ---------------------------------------------------------
SESSION_IDLE_TIMEOUT_MINUTES = env.int("SESSION_IDLE_TIMEOUT_MINUTES", default=30)
LOGIN_MAX_FAILED_ATTEMPTS = env.int("LOGIN_MAX_FAILED_ATTEMPTS", default=5)
LOGIN_LOCKOUT_MINUTES = env.int("LOGIN_LOCKOUT_MINUTES", default=15)

# --- Contrasenas (RNF02: Argon2 primero) ------------------------------------
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Zona horaria (RF14: hora de Colombia) ----------------------------------
LANGUAGE_CODE = "es-co"
TIME_ZONE = "America/Bogota"
USE_I18N = True
USE_TZ = True

# --- Archivos ---------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# /media NO se publica por URL: los archivos se sirven por un endpoint autenticado.
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "media")))
MEDIA_URL = None
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024  # solo cuerpo no-archivo; los archivos van a disco
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024

# --- DRF --------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["modules.accounts.authentication.SessionJWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "modules.core.permissions.PasswordChangeNotRequired",
    ],
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_PAGINATION_CLASS": "modules.core.pagination.StandardPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "modules.core.exceptions.api_exception_handler",
}
if HAS_SPECTACULAR:
    REST_FRAMEWORK["DEFAULT_SCHEMA_CLASS"] = "drf_spectacular.openapi.AutoSchema"

SPECTACULAR_SETTINGS = {
    "TITLE": "Mibers API",
    "DESCRIPTION": "API interna de gestion de contenido para redes sociales.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=10),
    "REFRESH_TOKEN_LIFETIME": timedelta(hours=12),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

# --- CORS (SPA separado) ----------------------------------------------------
CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

# --- WhatsApp Cloud API (numero de la agencia; RNF10: solo APIs oficiales) ---------------------
# "fake" simula los envios (desarrollo/pruebas); "cloud" usa la API real.
WHATSAPP_BACKEND = env("WHATSAPP_BACKEND", default="fake")
WHATSAPP_TOKEN = env("WHATSAPP_TOKEN", default="")
WHATSAPP_PHONE_NUMBER_ID = env("WHATSAPP_PHONE_NUMBER_ID", default="")
WHATSAPP_APP_SECRET = env("WHATSAPP_APP_SECRET", default="")
WHATSAPP_WEBHOOK_VERIFY_TOKEN = env("WHATSAPP_WEBHOOK_VERIFY_TOKEN", default="")
WHATSAPP_GRAPH_VERSION = env("WHATSAPP_GRAPH_VERSION", default="v21.0")
WHATSAPP_LANGUAGE = env("WHATSAPP_LANGUAGE", default="es")
# Nombres de las plantillas aprobadas en Meta Business (ver modules/notifications/gateway.py).
WHATSAPP_TEMPLATE_CLIENT_IMAGE = env("WHATSAPP_TEMPLATE_CLIENT_IMAGE", default="pieza_cliente_imagen")
WHATSAPP_TEMPLATE_CLIENT_VIDEO = env("WHATSAPP_TEMPLATE_CLIENT_VIDEO", default="pieza_cliente_video")
WHATSAPP_TEMPLATE_CLIENT_DOCUMENT = env(
    "WHATSAPP_TEMPLATE_CLIENT_DOCUMENT", default="pieza_cliente_documento"
)
WHATSAPP_TEMPLATE_ALERT = env("WHATSAPP_TEMPLATE_ALERT", default="alerta_director")
WHATSAPP_TEMPLATE_REMINDER = env("WHATSAPP_TEMPLATE_REMINDER", default="recordatorio_publicacion")

# --- Publicacion ---------------------------------------------------------------------------------
# Minutos sin confirmar una publicacion pendiente antes de alertar al administrador.
PUBLICATION_OVERDUE_MINUTES = env.int("PUBLICATION_OVERDUE_MINUTES", default=30)

# --- Meta Graph API (ADR0007) --------------------------------------------------------------------
# manual = publicacion asistida; graph = Graph API real; fake = adaptador de pruebas.
META_PUBLISHING = env("META_PUBLISHING", default="manual")
META_SYSTEM_USER_TOKEN = env("META_SYSTEM_USER_TOKEN", default="")
META_APP_SECRET = env("META_APP_SECRET", default="")
META_APP_ID = env("META_APP_ID", default="")
META_GRAPH_VERSION = env("META_GRAPH_VERSION", default="v25.0")
# URL publica del backend para que Meta descargue los archivos y vigencia de la firma.
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", default="http://localhost:8000")
PUBLIC_MEDIA_TTL_SECONDS = env.int("PUBLIC_MEDIA_TTL_SECONDS", default=3600)

# --- Seguridad de campos (RNF02: AES-256) -----------------------------------
# Clave de 32 bytes codificada en base64 urlsafe. Ver .env.example.
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    # No registrar URLs de httpx/httpcore: pueden incluir appsecret_proof.
    "loggers": {
        "httpx": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "httpcore": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
