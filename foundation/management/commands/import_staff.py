"""Load the existing staff list once, keeping each person's 5-digit number as their EMP code (synthetic demo data only)."""

import csv

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from foundation.models import BusinessUnit
from foundation.staff_records import import_existing_staff
from foundation.transaction_services import InvalidTransaction

COLUMNS = ["employee_number", "full_name", "arabic_name", "job_title", "department", "grade", "nationality", "date_of_birth",
           "work_email", "mobile", "joined_on", "contract", "contract_end", "probation_end"]


class Command(BaseCommand):
    help = "Import the existing staff list from a CSV file. Columns: " + ", ".join(COLUMNS)

    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("--unit", required=True, help="Business unit code, for example TVL")
        parser.add_argument("--user", required=True, help="Username of the person loading the list (needs the staff records permission)")

    def handle(self, *args, file, unit, user, **options):
        business_unit = BusinessUnit.objects.filter(code=unit).first()
        actor = get_user_model().objects.filter(username=user).first()
        if business_unit is None or actor is None:
            raise CommandError("Unknown business unit or user.")
        with open(file, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            missing = [column for column in ("employee_number", "full_name", "job_title", "department", "nationality", "joined_on")
                       if column not in (reader.fieldnames or [])]
            if missing:
                raise CommandError("The file is missing columns: " + ", ".join(missing))
            rows = [{key: (value or "").strip() for key, value in row.items() if key in COLUMNS} for row in reader]
        try:
            result = import_existing_staff(actor=actor, business_unit=business_unit, rows=rows)
        except InvalidTransaction as error:
            raise CommandError(f"Nothing was imported. {error}") from None
        self.stdout.write(f"Imported {result['added']} people. The next person added gets {result['next_number']}.")
