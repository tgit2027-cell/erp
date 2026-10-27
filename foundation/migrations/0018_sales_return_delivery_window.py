from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


def confirm_delivery_basis(apps, schema_editor):
    alias = schema_editor.connection.alias
    apps.get_model("foundation", "SalesReturnPolicy").objects.using(alias).update(date_basis="delivery")
    Intake = apps.get_model("foundation", "SalesReturnIntake")
    for intake in Intake.objects.using(alias).select_related("quote").iterator():
        # Preserve historical policy snapshots, audit facts, idempotency results
        # and workflow state. Assess only from actual delivery and captured days.
        days = intake.policy_snapshot.get("provisional_calendar_days", 14)
        delivered_at = intake.quote.delivered_at
        if delivered_at is None:
            window = {
                "date_basis": "delivery", "calendar_days": days, "delivery_date": None,
                "deadline": None, "request_date": intake.request_date.isoformat(),
                "eligible": None, "message": "Actual delivery is unavailable; date-window eligibility cannot be assessed.",
            }
        else:
            delivered = timezone.localdate(delivered_at)
            deadline = delivered + timedelta(days=days)
            eligible = delivered <= intake.request_date <= deadline
            window = {
                "date_basis": "delivery", "calendar_days": days,
                "delivery_date": delivered.isoformat(), "deadline": deadline.isoformat(),
                "request_date": intake.request_date.isoformat(), "eligible": eligible,
                "message": (
                    "Historical intake assessed using actual delivery and its captured day count. "
                    f"Delivery {delivered.isoformat()}; inclusive deadline {deadline.isoformat()}. "
                    + ("Within date window; inspection and Finance review remain required."
                       if eligible else "Outside delivery-date window; no exception or disposition is authorized.")
                ),
            }
        Intake.objects.using(alias).filter(pk=intake.pk).update(delivery_window=window)


def restore_unconfigured_basis(apps, schema_editor):
    apps.get_model("foundation", "SalesReturnPolicy").objects.using(
        schema_editor.connection.alias,
    ).update(date_basis="unconfigured")


class Migration(migrations.Migration):
    dependencies = [("foundation", "0017_synthetic_sales_return_intake")]
    operations = [
        migrations.RemoveConstraint(model_name="salesreturnpolicy", name="return_basis_unconfigured"),
        migrations.AlterField(
            model_name="salesreturnpolicy", name="date_basis",
            field=models.CharField(default="delivery", editable=False, max_length=24),
        ),
        migrations.AddField(
            model_name="salesreturnintake", name="delivery_window", field=models.JSONField(default=dict),
        ),
        migrations.RunPython(confirm_delivery_basis, restore_unconfigured_basis),
        migrations.AddConstraint(
            model_name="salesreturnpolicy",
            constraint=models.CheckConstraint(condition=models.Q(date_basis="delivery"), name="return_basis_delivery"),
        ),
    ]
