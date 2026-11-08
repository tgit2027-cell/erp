"""Test helper for the setup lock (Nov 2026): run a setup change as the Master Admin.

Tests that only need a bank account, a letter template or a similar piece of setup in place call the
service through `by_master_admin`, which lets the given person act as the Master Admin for that one call.
"""

from functools import wraps


def by_master_admin(service):
    @wraps(service)
    def run(*args, actor, **kwargs):
        model = type(actor)
        was = actor.is_superuser
        model.objects.filter(pk=actor.pk).update(is_superuser=True)
        actor.is_superuser = True
        try:
            return service(*args, actor=actor, **kwargs)
        finally:
            model.objects.filter(pk=actor.pk).update(is_superuser=was)
            actor.is_superuser = was
    return run
