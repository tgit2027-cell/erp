"""Which records a request or background job created, changed or deleted.

A collector lives for one API request or one management command. Model signals
add every saved or deleted record to it. Master data (items, prices, parties,
locations, accounts and permissions) also keeps the old and new value of each
changed field, so the log shows what changed and not only that something did.
"""

from contextvars import ContextVar
from decimal import Decimal

from django.apps import apps
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save

current_changes = ContextVar("audit_current_changes", default=None)

TRACKED_APPS = {"foundation"}
# Master data whose field values are kept before and after each change.
DETAILED = {
    "foundation.BusinessUnit", "foundation.Location", "foundation.Item", "foundation.Variant",
    "foundation.ApprovedSellingPrice", "foundation.SyntheticCounterparty", "foundation.LedgerAccount",
    "foundation.Currency", "foundation.UserActionGrant", "foundation.BusinessUnitRoleAssignment",
    "foundation.LocationCapacityGrant", "foundation.ReportingRoleMembership", "foundation.ReportPanelGrant",
    "foundation.ReportDataGrant", "foundation.WorkflowPolicyConfig", "foundation.SpecialOrderValueBand",
}
HIDDEN_WORDS = ("password", "token", "secret")
MAX_IDS = 25
MAX_CHANGES = 50


def _label(model):
    return f"{model._meta.app_label}.{model._meta.object_name}"


def _plain(value):
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _values(instance):
    return {
        field.attname: _plain(getattr(instance, field.attname))
        for field in instance._meta.concrete_fields
        if not any(word in field.name for word in HIDDEN_WORDS)
    }


class Collector:
    def __init__(self):
        self.records = {}
        self.changes = []
        self.dropped_changes = 0

    def touched(self, label, action, pk):
        row = self.records.setdefault(label, {"created": 0, "updated": 0, "deleted": 0, "ids": []})
        row[action] += 1
        if pk is not None and len(row["ids"]) < MAX_IDS and pk not in row["ids"]:
            row["ids"].append(pk)

    def change(self, entry):
        if len(self.changes) < MAX_CHANGES:
            self.changes.append(entry)
        else:
            self.dropped_changes += 1

    def __bool__(self):
        return bool(self.records)

    def summary(self):
        detail = {"records": self.records}
        if self.changes:
            detail["changes"] = self.changes
        if self.dropped_changes:
            detail["more_changes"] = self.dropped_changes
        return detail


def _collector(sender, raw=False):
    collector = current_changes.get()
    if collector is None or raw or sender._meta.app_label not in TRACKED_APPS:
        return None
    return collector


def _before(sender, instance, raw=False, **kwargs):
    if _collector(sender, raw) is None or _label(sender) not in DETAILED or not instance.pk:
        return
    old = sender._base_manager.filter(pk=instance.pk).first()
    instance._audit_old_values = _values(old) if old is not None else None


def _saved(sender, instance, created, raw=False, **kwargs):
    collector = _collector(sender, raw)
    if collector is None:
        return
    label = _label(sender)
    collector.touched(label, "created" if created else "updated", instance.pk)
    if label not in DETAILED:
        return
    new = _values(instance)
    if created:
        collector.change({"model": label, "id": instance.pk, "action": "created", "values": new})
        return
    old = getattr(instance, "_audit_old_values", None) or {}
    changed = {key: [old.get(key), value] for key, value in new.items() if old.get(key) != value}
    if changed:
        collector.change({"model": label, "id": instance.pk, "action": "updated", "changed": changed})


def _deleted(sender, instance, **kwargs):
    collector = _collector(sender)
    if collector is None:
        return
    label = _label(sender)
    collector.touched(label, "deleted", instance.pk)
    if label in DETAILED:
        collector.change({"model": label, "id": instance.pk, "action": "deleted", "values": _values(instance)})


def _links_changed(sender, instance, action, model, pk_set, **kwargs):
    collector = _collector(type(instance))
    label = _label(type(instance))
    if collector is None or label not in DETAILED or action not in {"post_add", "post_remove", "post_clear"}:
        return
    collector.touched(label, "updated", instance.pk)
    collector.change({
        "model": label, "id": instance.pk, "action": action.removeprefix("post_"),
        "linked": _label(model), "ids": sorted(pk_set or []),
    })


def connect():
    pre_save.connect(_before, dispatch_uid="audit_changes_before")
    post_save.connect(_saved, dispatch_uid="audit_changes_saved")
    post_delete.connect(_deleted, dispatch_uid="audit_changes_deleted")
    for label in DETAILED:
        model = apps.get_model(label)
        for field in model._meta.many_to_many:
            m2m_changed.connect(_links_changed, sender=field.remote_field.through,
                                dispatch_uid=f"audit_links_{label}_{field.name}")
