import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("finance_allocation_lab", "0003_reconciliation_proposals")]
    operations = [
        migrations.CreateModel(
            name="ExpenseProposal",
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
                models.CheckConstraint(condition=models.Q(is_synthetic=True), name="lab_expense_synthetic"),
                models.CheckConstraint(condition=models.Q(revision__gte=1), name="lab_expense_revision_positive"),
            ]},
        ),
        migrations.CreateModel(
            name="ExpenseRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("revision", models.PositiveIntegerField()),
                ("snapshot", models.JSONField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("proposal", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                               related_name="revisions", to="finance_allocation_lab.expenseproposal")),
            ],
            options={"default_permissions": (), "constraints": [
                models.UniqueConstraint(fields=("proposal", "revision"), name="lab_expense_revision_unique"),
            ]},
        ),
        migrations.RunSQL("""
            CREATE FUNCTION lab_expense_revision_immutable() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'Synthetic expense proposal history is immutable';
            END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER lab_expense_revision_guard
            BEFORE UPDATE OR DELETE ON finance_allocation_lab_expenserevision
            FOR EACH ROW EXECUTE FUNCTION lab_expense_revision_immutable();
        """, """
            DROP TRIGGER lab_expense_revision_guard ON finance_allocation_lab_expenserevision;
            DROP FUNCTION lab_expense_revision_immutable();
        """),
    ]
