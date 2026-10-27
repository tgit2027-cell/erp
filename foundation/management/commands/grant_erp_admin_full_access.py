"""Grant full Django and business-unit action access to the synthetic erp_admin.

Restricted to the explicitly configured Render demo and to an account already
marked as synthetic. This does not bypass module-specific authorization checks
that use separate grant models.
"""
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from foundation.models import BusinessUnit, DemoUserMarker, UserActionGrant


class Command(BaseCommand):
    help = "Grant erp_admin Django superuser access and all business-unit action grants."

    @transaction.atomic
    def handle(self, *args, **options):
        if os.environ.get("APP_ENV") != "demo" or not os.environ.get("RENDER"):
            raise CommandError(
                "Restricted to Render's explicitly configured synthetic demo (APP_ENV=demo)."
            )

        User = get_user_model()
        user = User.objects.select_for_update().filter(username="erp_admin").first()
        if user is None:
            raise CommandError("User erp_admin does not exist.")
        if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
            raise CommandError(
                "erp_admin is not marked as a synthetic demo user; refusing to modify it."
            )

        user.is_active = True
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_active", "is_staff", "is_superuser"])

        # Explicit model permissions are useful to integrations and custom views
        # that inspect user_permissions rather than relying on is_superuser.
        user.user_permissions.set(Permission.objects.all())

        now = timezone.now()
        actions = [choice[0] for choice in UserActionGrant.Action.choices]
        units = BusinessUnit.objects.filter(is_active=True)
        created = 0
        updated = 0
        for unit in units:
            for action in actions:
                grant, was_created = UserActionGrant.objects.get_or_create(
                    user=user,
                    business_unit=unit,
                    action=action,
                    defaults={"starts_at": now, "ends_at": None, "revoked_at": None},
                )
                if was_created:
                    created += 1
                elif grant.revoked_at is not None or grant.ends_at is not None:
                    grant.revoked_at = None
                    grant.ends_at = None
                    grant.starts_at = now
                    grant.save(update_fields=["revoked_at", "ends_at", "starts_at"])
                    updated += 1

        self.stdout.write(self.style.SUCCESS(
            f"erp_admin is now an active Django superuser. "
            f"Granted {created} new business-unit actions; reactivated {updated}; "
            f"active business units checked: {units.count()}."
        ))
        self.stdout.write(
            "Note: separate module-specific grant models may still require their own grants."
        )
