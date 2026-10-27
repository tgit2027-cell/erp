"""Minimal disposable-only fixture, no demo seed, memberships or grants."""
import hashlib
import json
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from foundation.models import LegalEntity, BusinessUnit


def prepare():
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('data_directory'), current_setting('server_version')")
        directory, version = cursor.fetchone()
    assert directory == settings.FINANCE_LAB_DATA_DIRECTORY and version.startswith("16.15")
    assert str(connection.settings_dict["NAME"]).startswith("test_")
    entity = LegalEntity.objects.create(code="test-finance-lab", name="TEST ONLY — synthetic Finance entity")
    BusinessUnit.objects.create(legal_entity=entity, code="TEST-BU-A", name="Synthetic unit A")
    BusinessUnit.objects.create(legal_entity=entity, code="TEST-BU-B", name="Synthetic unit B")
    get_user_model().objects.create_user(username=settings.FINANCE_LAB_ACTOR, password=None)
    print("Minimal synthetic lab fixture created: one entity, two BU dimensions, one unusable-password nonstaff test actor; zero grants.")


def set_actor_active(active):
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('data_directory')")
        assert cursor.fetchone()[0] == settings.FINANCE_LAB_DATA_DIRECTORY
    assert str(connection.settings_dict["NAME"]).startswith("test_")
    get_user_model().objects.filter(username=settings.FINANCE_LAB_ACTOR).update(is_active=active)


def operational_digest():
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('data_directory')")
        assert cursor.fetchone()[0] == settings.FINANCE_LAB_DATA_DIRECTORY
    rows = {
        model._meta.label: list(model.objects.order_by("pk").values())
        for model in apps.get_app_config("foundation").get_models()
        if not model.__name__.startswith("FinanceDraft")
    }
    actor = get_user_model().objects.get(username=settings.FINANCE_LAB_ACTOR)
    rows["test_actor_authority"] = {
        "active": actor.is_active, "staff": actor.is_staff, "superuser": actor.is_superuser,
        "usable_password": actor.has_usable_password(),
        "groups": list(actor.groups.order_by("pk").values_list("pk", flat=True)),
        "permissions": list(actor.user_permissions.order_by("pk").values_list("pk", flat=True)),
    }
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()
