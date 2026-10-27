from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("finance_allocation_lab", "0011_supplier_advance_drafts"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(
            name="DraftCreateReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("request_id", models.UUIDField()),
                ("resource", models.CharField(max_length=40)),
                ("fingerprint", models.CharField(max_length=64)),
                ("response", models.JSONField(default=dict)),
                ("status", models.PositiveSmallIntegerField(default=0)),
                ("is_synthetic", models.BooleanField(default=True, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ("legal_entity", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="foundation.legalentity")),
            ],
            options={"default_permissions": (), "constraints": [
                models.UniqueConstraint(fields=("actor", "legal_entity", "request_id"), name="lab_create_request_identity"),
                models.CheckConstraint(condition=models.Q(is_synthetic=True), name="lab_create_receipt_synthetic"),
            ]},
        ),
    ]
