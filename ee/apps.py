from django.apps import AppConfig


class EnterpriseConfig(AppConfig):
    name = "ee"
    verbose_name = "PostHog EE (FOSS stubs)"
    default_auto_field = "django.db.models.BigAutoField"
