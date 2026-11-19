import tempfile

from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "test-secret-key-with-at-least-32-bytes-length"
ALLOWED_HOSTS = ["testserver", "localhost"]
# Por defecto los tests corren en SQLite; con DATABASE_URL apuntando a Postgres
# tambien se ejecutan las pruebas especificas de Postgres (trigger, SKIP LOCKED).
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
MEDIA_ROOT = tempfile.mkdtemp(prefix="mibers-test-media-")
FIELD_ENCRYPTION_KEY = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="  # 32 bytes
DATABASES["default"]["CONN_MAX_AGE"] = 0  # noqa: F405
