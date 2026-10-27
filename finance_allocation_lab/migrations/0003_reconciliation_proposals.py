import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("finance_allocation_lab", "0002_budget_scenarios")]
    operations = [
        migrations.CreateModel(
            name="ReconciliationProposal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payload", models.JSONField()),
                ("revision", models.PositiveIntegerField(default=1)),
                ("is_synthetic", models.BooleanField(default=True, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("legal_entity", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="foundation.legalentity")),
            ],
            options={"default_permissions": (), "constraints": [
                models.CheckConstraint(condition=models.Q(is_synthetic=True), name="lab_recon_synthetic"),
                models.CheckConstraint(condition=models.Q(revision__gte=1), name="lab_recon_revision_positive"),
            ]},
        ),
        migrations.CreateModel(
            name="ReconciliationRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("revision", models.PositiveIntegerField()),
                ("snapshot", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("proposal", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                               related_name="revisions", to="finance_allocation_lab.reconciliationproposal")),
            ],
            options={"default_permissions": (), "constraints": [
                models.UniqueConstraint(fields=("proposal", "revision"), name="lab_recon_revision_unique"),
            ]},
        ),
        migrations.RunSQL("""
            CREATE FUNCTION lab_recon_revision_immutable() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Synthetic reconciliation revision history is immutable';
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER lab_recon_revision_guard
            BEFORE UPDATE OR DELETE ON finance_allocation_lab_reconciliationrevision
            FOR EACH ROW EXECUTE FUNCTION lab_recon_revision_immutable();
        """, """
            DROP TRIGGER lab_recon_revision_guard ON finance_allocation_lab_reconciliationrevision;
            DROP FUNCTION lab_recon_revision_immutable();
        """),
    ]
