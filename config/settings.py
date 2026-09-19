import os
from pathlib import Path
import environ

env = environ.Env(
    DEBUG=(bool, False),
    LANGUAGE_CODE=(str, "es-pe"),
    TIME_ZONE=(str, "America/Lima"),
    DEFAULT_AI_PROVIDER=(str, "gemini"),
)

BASE_DIR = Path(__file__).resolve().parent.parent
environ.Env.read_env(os.path.join(BASE_DIR, ".env"))

SECRET_KEY = env("SECRET_KEY", default="dev-secret-change-me")
DEBUG = env.bool("DEBUG", default=True)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["127.0.0.1", "localhost", "0.0.0.0"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "corsheaders",
    "django_extensions",
    "django_celery_results",
    "django_celery_beat",
    "core",
]

AUTHENTICATION_BACKENDS = [
    "core.auth_backend.LegacyUserBackend",
    "django.contrib.auth.backends.ModelBackend",
]
LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
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
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.app_version",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = env("LANGUAGE_CODE")
TIME_ZONE = env("TIME_ZONE")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / env("STATIC_ROOT", default="staticfiles")
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / env("MEDIA_ROOT", default="media")
IMAGES_ROOT = BASE_DIR / env("IMAGES_ROOT", default="media/images")
os.makedirs(IMAGES_ROOT, exist_ok=True)
os.makedirs(MEDIA_ROOT, exist_ok=True)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOW_ALL_ORIGINS = DEBUG
CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "https://elecciones2026.whadox.com", "https://*.whadox.com"]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.OrderingFilter",
        "rest_framework.filters.SearchFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{asctime} {levelname} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
        "file_core": {
            "class": "logging.FileHandler",
            "filename": BASE_DIR / "logs" / "core.log",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "core": {"handlers": ["console", "file_core"], "level": "INFO", "propagate": True},
        "django": {"handlers": ["console"], "level": "INFO"},
    },
}
os.makedirs(BASE_DIR / "logs", exist_ok=True)

REDIS_URL = env("REDIS_URL", default="redis://127.0.0.1:6379/0")
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://127.0.0.1:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="django-db")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ALWAYS_EAGER = DEBUG
CELERY_TASK_EAGER_PROPAGATES = DEBUG
CELERY_BEAT_SCHEDULE = {
    "auto_dispatch_pending_batch": {
        "task": "core.tasks.auto_dispatch_pending_batch",
        "schedule": 15,  # segundos base; el interior lee ProcessingConfig.auto_batch_enabled y solo actúa si está ON
    },
}

MASTER_ENCRYPTION_KEY = env("MASTER_ENCRYPTION_KEY", default="dev-master-key-change-me-32-bytes!")
TESSERACT_CMD = env("TESSERACT_CMD", default="")

DEFAULT_AI_PROVIDER = env("DEFAULT_AI_PROVIDER", default="gemini")
GEMINI_API_KEY = env("GEMINI_API_KEY", default="")
GEMINI_MODEL = env("GEMINI_MODEL", default="gemini-1.5-pro-latest")
DEEPSEEK_API_KEY = env("DEEPSEEK_API_KEY", default="")
DEEPSEEK_MODEL = env("DEEPSEEK_MODEL", default="deepseek-chat")

ADMIN_USERNAME = env("ADMIN_USERNAME", default="admin")
ADMIN_EMAIL = env("ADMIN_EMAIL", default="admin@actas.local")
ADMIN_PASSWORD = env("ADMIN_PASSWORD", default="Admin12345")
