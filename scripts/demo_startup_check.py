"""Read-only checks executed by dev.sh after migrate --check, before HTTP starts."""
import os
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from foundation.models import DemoUserMarker

assert settings.APP_ENV == "demo", "Only APP_ENV=demo is permitted."
database = settings.DATABASES["default"]
assert database["HOST"] in {"127.0.0.1", "localhost", "::1"}, "Only loopback databases are permitted."
assert database["NAME"] == "tivoli_erp_demo", "The established synthetic demo database is required."
with transaction.atomic():
    with connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cursor.execute("SELECT current_database(), current_setting('data_directory')")
        name, directory = cursor.fetchone()
        assert name == "tivoli_erp_demo"
        if os.environ.get("USE_BUNDLED_POSTGRES", "1") == "1":
            assert Path(directory).resolve() == Path(os.environ["PGDATA"]).resolve(), "Unexpected PostgreSQL cluster."
    users = get_user_model().objects
    assert users.exists(), "No existing demo accounts; startup will not seed them."
    assert not users.exclude(pk__in=DemoUserMarker.objects.values("user_id")).exists(), "Unmarked users; demo provenance is uncertain."
    checked = 0
    for model in apps.get_app_config("foundation").get_models():
        if any(field.name == "is_synthetic" for field in model._meta.fields):
            assert not model.objects.filter(is_synthetic=False).exists(), "Non-synthetic records; refusing startup."
            checked += 1
print(f"Existing synthetic demo verified read-only ({checked} synthetic-flag models); no migration or seed performed.")
