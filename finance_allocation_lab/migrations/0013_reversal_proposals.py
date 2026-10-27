import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("finance_allocation_lab", "0012_draft_create_receipts")]
    operations = [
        migrations.CreateModel(
            name="ReversalProposal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payload", models.JSONField()),
                ("revision", models.PositiveIntegerField(default=1)),
                ("is_synthetic", models.BooleanField(default=True, editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("legal_entity", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="foundation.legalentity")),
                ("source_record", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="foundation.financedraftjournalrevision")),
            ],
            options={"default_permissions": (), "constraints": [
                models.CheckConstraint(condition=models.Q(is_synthetic=True), name="lab_reversal_synthetic"),
                models.CheckConstraint(condition=models.Q(revision__gte=1), name="lab_reversal_positive"),
            ]},
        ),
        migrations.CreateModel(
            name="ReversalRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("revision", models.PositiveIntegerField()),
                ("snapshot", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("draft", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="revisions", to="finance_allocation_lab.reversalproposal")),
                ("source_record", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="foundation.financedraftjournalrevision")),
            ],
            options={"default_permissions": (), "constraints": [
                models.UniqueConstraint(fields=("draft", "revision"), name="lab_reversal_revision_unique"),
            ]},
        ),
        migrations.RunSQL("""
            CREATE FUNCTION lab_reversal_rev_immutable() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Synthetic reversal proposal history is immutable';
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER lab_reversal_rev_guard
            BEFORE UPDATE OR DELETE ON finance_allocation_lab_reversalrevision
            FOR EACH ROW EXECUTE FUNCTION lab_reversal_rev_immutable();
        """, reverse_sql="""
            DROP TRIGGER IF EXISTS lab_reversal_rev_guard ON finance_allocation_lab_reversalrevision;
            DROP FUNCTION IF EXISTS lab_reversal_rev_immutable();
        """),
    ]
