"""Record management commands and scheduled jobs that change data.

Jobs do not pass through the API, so the request middleware never sees them.
Every command runs with its own change collector, and one that saved or
deleted records leaves a "Background job" event naming the command, what it
touched and whether it finished.
"""

from django.core.management.base import BaseCommand

from .changes import Collector, current_changes

# Commands that build or inspect the schema or run the test suite.
SKIPPED = {"test", "migrate", "makemigrations", "sqlmigrate", "showmigrations", "check", "runserver",
           "collectstatic", "flush", "shell_plus"}


def _record(name, changes, error):
    from .models import AuditEvent
    from .recording import record

    detail = {"command": name, "succeeded": error is None, **changes.summary()}
    if error is not None:
        detail["error"] = type(error).__name__
    record(AuditEvent.Kind.SYSTEM_JOB, username="system", detail=detail)


def install():
    original = BaseCommand.execute
    if getattr(original, "_audited", False):
        return

    def execute(self, *args, **options):
        name = self.__module__.rsplit(".", 1)[-1]
        if name in SKIPPED or current_changes.get() is not None:
            return original(self, *args, **options)
        changes = Collector()
        token = current_changes.set(changes)
        error = None
        try:
            return original(self, *args, **options)
        except BaseException as exc:
            error = exc
            raise
        finally:
            current_changes.reset(token)
            if changes:
                _record(name, changes, error)

    execute._audited = True
    BaseCommand.execute = execute
