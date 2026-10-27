from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from foundation.models import DemoUserMarker


class Command(BaseCommand):
    help = "Set an interactive, non-echoed password for a synthetic demo account."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)

    def handle(self, *args, **options):
        import os

        if os.environ.get("APP_ENV") != "demo":
            raise CommandError("Refusing to set demo credentials unless APP_ENV=demo.")
        User = get_user_model()
        user = User.objects.filter(username=options["username"]).first()
        if user is None or not DemoUserMarker.objects.filter(user=user).exists():
            raise CommandError("That synthetic demo username does not exist.")
        password = getpass("New password (input hidden): ")
        confirmation = getpass("Confirm password (input hidden): ")
        if password != confirmation:
            raise CommandError("Passwords do not match.")
        try:
            validate_password(password, user)
        except ValidationError as error:
            raise CommandError("Password does not meet the configured policy: " + "; ".join(error.messages))
        user.set_password(password)
        user.save(update_fields=["password"])
        self.stdout.write(self.style.SUCCESS("Synthetic demo password set."))
