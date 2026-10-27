import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from foundation.first_proof import run_first_proof


class Command(BaseCommand):
    help = (
        "Run the first full proof (EUR purchase, USD shipping, QAR customs, sale with deposit, "
        "delivery with invoice, reconciliation) on the local synthetic demo data. Safe to run again."
    )

    def handle(self, *args, **options):
        if os.environ.get("APP_ENV") != "demo":
            raise CommandError("Refusing to run unless APP_ENV=demo is explicitly set.")
        if connection.settings_dict.get("HOST", "") not in {"", "localhost", "127.0.0.1"}:
            raise CommandError("Refusing to seed a non-local database.")
        admin = get_user_model().objects.filter(username="demo_admin", is_staff=True).first()
        if admin is None:
            raise CommandError("Run seed_demo first; a staff demo_admin is required.")
        result = run_first_proof(admin=admin)
        for number, step in enumerate(result["steps"], start=1):
            self.stdout.write(f"{number}. {step['step']}: {step['detail']}")
        if result["reconciliation"]["all_match"]:
            self.stdout.write(self.style.SUCCESS("The first full proof reconciles."))
        else:
            raise CommandError("The scenario ran but does not reconcile; open Reports > Scenario reconciliation.")
