import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("finance_allocation_lab", "0009_customer_invoice_drafts")]
    operations = [
        migrations.CreateModel(
            name="CustomerCreditDraft",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payload", models.JSONField()),
                ("invoice_reference_snapshot", models.JSONField(null=True)),
                ("revision", models.PositiveIntegerField(default=1)),
                ("is_synthetic", models.BooleanField(default=True, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("legal_entity", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="foundation.legalentity")),
                ("linked_invoice", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                                     to="finance_allocation_lab.customerinvoicedraft")),
            ],
            options={"default_permissions": (), "constraints": [
                models.CheckConstraint(condition=models.Q(is_synthetic=True), name="lab_ar_credit_synthetic"),
                models.CheckConstraint(condition=models.Q(revision__gte=1), name="lab_ar_credit_revision_positive"),
            ]},
        ),
        migrations.CreateModel(
            name="CustomerCreditRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("revision", models.PositiveIntegerField()),
                ("snapshot", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("draft", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                           related_name="revisions", to="finance_allocation_lab.customercreditdraft")),
            ],
            options={"default_permissions": (), "constraints": [
                models.UniqueConstraint(fields=("draft", "revision"), name="lab_ar_credit_revision_unique"),
            ]},
        ),
        migrations.RunSQL("""
            CREATE FUNCTION lab_ar_credit_revision_immutable() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Synthetic customer credit proposal history is immutable';
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER lab_ar_credit_revision_guard
            BEFORE UPDATE OR DELETE ON finance_allocation_lab_customercreditrevision
            FOR EACH ROW EXECUTE FUNCTION lab_ar_credit_revision_immutable();
        """, """
            DROP TRIGGER lab_ar_credit_revision_guard ON finance_allocation_lab_customercreditrevision;
            DROP FUNCTION lab_ar_credit_revision_immutable();
        """),
    ]
