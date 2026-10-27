"""Disposable acceptance fixture only. Execute through manage.py shell."""
import json
import os
import secrets
from pathlib import Path

from django.db import connection
from django.contrib.auth import get_user_model
from foundation.models import UserFavorite, UserActionGrant, DemoUserMarker, BusinessUnit
from foundation.test_purchasing_workflows import PurchasingFixtureMixin

with connection.cursor() as cursor:
    cursor.execute("select current_setting('data_directory'), current_setting('server_version')")
    directory, version = cursor.fetchone()
assert directory == os.environ["EXPECTED_DISPOSABLE_DATA"] and version.startswith("16.15")
assert os.environ["APP_ENV"] == "demo"
root = Path(os.environ["ACCEPTANCE_ROOT"])
snapshot = root / "migration-snapshot.json"

def evidence():
    return {
        "favorites": list(UserFavorite.objects.order_by("pk").values("pk", "user_id", "destination")),
        "grants": list(UserActionGrant.objects.order_by("pk").values("pk", "user_id", "business_unit_id", "action")),
    }

if os.environ["ACCEPTANCE_PHASE"] == "prepare":
    class Fixture(PurchasingFixtureMixin):
        pass
    fixture = Fixture()
    fixture.create_fixture()
    fixture.make_receipt(key="REAL-BROWSER-SYNTHETIC-RECEIPT")
    quote = fixture.make_quote(key="REAL-BROWSER-SYNTHETIC-QUOTE")
    operator = get_user_model().objects.create_user(
        username="synthetic_browser_operator", is_staff=True, is_superuser=True,
    )
    DemoUserMarker.objects.create(user=operator, is_synthetic=True)
    for unit in BusinessUnit.objects.all():
        UserActionGrant.objects.create(user=operator, business_unit=unit, action="read_stock")
    credentials = {}
    for user in (fixture.sales, fixture.roche_user, fixture.admin, operator):
        password = secrets.token_urlsafe(32)
        user.set_password(password)
        user.save(update_fields=["password"])
        credentials[user.username] = password
    UserFavorite.objects.create(user=fixture.sales, destination="purchasing")
    UserFavorite.objects.create(user=fixture.roche_user, destination="inventory")
    (root / "browser-private.json").write_text(json.dumps({
        "credentials": credentials, "quote_id": quote["quote_id"],
        "unit": fixture.unit.pk, "roche": fixture.roche.pk,
        "location": fixture.location.pk, "variant": fixture.variant.pk,
    }))
    (root / "browser-private.json").chmod(0o600)
    snapshot.write_text(json.dumps(evidence()))
    print("Synthetic fixture prepared with current ORM before the legacy-0018 upgrade rehearsal; private test credentials not logged")
else:
    assert evidence() == json.loads(snapshot.read_text()), "Migration changed existing Favorites or grants"
    with connection.cursor() as cursor:
        cursor.execute("select count(*) from django_migrations where app='foundation' and name='0019_alter_userfavorite_destination'")
        assert cursor.fetchone()[0] == 1
    print("0018 -> 0019: existing section Favorites, ownership and grant rows unchanged")
