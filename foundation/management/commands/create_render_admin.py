"""Create the first synthetic Render demo administrator without echoing credentials."""

import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from foundation.models import DemoUserMarker


class Command(BaseCommand):
    help = "Create the initial erp_admin account for an explicitly initialized Render demo."

    @transaction.atomic
    def handle(self, *args, **options):
        if os.environ.get("APP_ENV") != "demo" or not os.environ.get("RENDER"):
            raise CommandError("This bootstrap is restricted to Render's synthetic demo deployment.")

        User = get_user_model()
        user = User.objects.select_for_update().filter(username="erp_admin").first()
        if user is not None:
            if not DemoUserMarker.objects.filter(user=user, is_synthetic=True).exists():
                raise CommandError("Existing erp_admin account is not marked as synthetic; refusing changes.")
            if not user.is_active or not user.is_staff or not user.is_superuser:
                raise CommandError("Existing synthetic erp_admin has unexpected access settings; refusing changes.")
            self.stdout.write("Synthetic erp_admin already exists; left its credentials unchanged.")
            return

        password = os.environ.get("RENDER_INITIAL_ADMIN_PASSWORD", "")
        if not password:
            raise CommandError("Set RENDER_INITIAL_ADMIN_PASSWORD before the first Render bootstrap.")
        user = User(username="erp_admin", first_name="ERP", last_name="Administrator", is_staff=True, is_superuser=True)
        try:
            validate_password(password, user)
        except ValidationError as error:
            raise CommandError("Initial administrator password does not meet policy: " + "; ".join(error.messages)) from None
        user.set_password(password)
        user.save()
        DemoUserMarker.objects.create(user=user)
        self.stdout.write(self.style.SUCCESS("Created the synthetic Render erp_admin superuser."))
