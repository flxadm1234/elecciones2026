from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Actas Electorales"

    def ready(self):
        from . import signals  # noqa: F401
        try:
            self._ensure_ai_provider_defaults()
        except Exception:
            import logging as _l
            _l.getLogger(__name__).warning("AIProviderDefaults bootstrap skip (migrations running?)", exc_info=True)

    def _ensure_ai_provider_defaults(self) -> None:
        from .models import AIProviderSettings
        try:
            count = AIProviderSettings.objects.count()
        except Exception:
            return
        if count > 0:
            return
        try:
            AIProviderSettings.objects.get_or_create(
                provider="gemini",
                defaults={
                    "is_default": True,
                    "is_active": False,
                    "model": "gemini-3.6-flash",
                    "temperature": 0.0,
                    "timeout_secs": 60,
                    "max_retries": 3,
                    "confidence_threshold": 0.85,
                    "fallback_to": "",
                },
            )
            AIProviderSettings.objects.get_or_create(
                provider="deepseek",
                defaults={
                    "is_default": False,
                    "is_active": False,
                    "model": "deepseek-flash",
                    "temperature": 0.0,
                    "timeout_secs": 60,
                    "max_retries": 3,
                    "confidence_threshold": 0.85,
                    "fallback_to": "",
                },
            )
        except Exception:
            import logging as _l
            _l.getLogger(__name__).warning("AIProviderDefaults create skip", exc_info=True)
