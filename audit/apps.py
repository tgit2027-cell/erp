from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "audit"
    verbose_name = "Security audit log"

    def ready(self):
        from . import changes, jobs, signals  # noqa: F401

        changes.connect()
        jobs.install()
