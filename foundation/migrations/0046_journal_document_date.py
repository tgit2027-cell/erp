import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

# Posted entries are protected by the foundation_journalentry_immutable trigger.
# The backfill switches it off for this one statement only; constraints are made
# immediate first so the balance checks run before the columns are altered.
BACKFILL = """
SET CONSTRAINTS ALL IMMEDIATE;
ALTER TABLE foundation_journalentry DISABLE TRIGGER foundation_journalentry_immutable;
UPDATE foundation_journalentry AS entry
SET document_date = COALESCE(
    (SELECT payment.paid_on FROM foundation_supplierpayment AS payment WHERE payment.journal_entry_id = entry.id),
    (COALESCE(entry.posted_at, entry.created_at) AT TIME ZONE 'Asia/Qatar')::date
);
UPDATE foundation_journalentry AS entry
SET business_unit_id = (
    SELECT unit.id FROM foundation_businessunit AS unit WHERE unit.legal_entity_id = entry.legal_entity_id
)
WHERE entry.business_unit_id IS NULL
  AND (SELECT COUNT(*) FROM foundation_businessunit AS unit WHERE unit.legal_entity_id = entry.legal_entity_id) = 1;
ALTER TABLE foundation_journalentry ENABLE TRIGGER foundation_journalentry_immutable;
"""


def require_units(apps, schema_editor):
    JournalEntry = apps.get_model("foundation", "JournalEntry")
    missing = JournalEntry.objects.filter(business_unit__isnull=True).count()
    if missing:
        raise RuntimeError(
            f"{missing} journal entries have no business unit and their company has several. "
            "Assign them before running this migration."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("foundation", "0045_general_ledger_permission"),
    ]

    operations = [
        migrations.AddField(
            model_name="journalentry",
            name="document_date",
            field=models.DateField(null=True),
        ),
        migrations.RunSQL(BACKFILL, reverse_sql=migrations.RunSQL.noop),
        migrations.RunPython(require_units, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="journalentry",
            name="document_date",
            field=models.DateField(
                default=django.utils.timezone.localdate,
                help_text="Accounting date: the date of the source document (payment, receipt, delivery), not the posting time.",
            ),
        ),
        migrations.AlterField(
            model_name="journalentry",
            name="business_unit",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="report_journal_entries",
                to="foundation.businessunit",
            ),
        ),
        migrations.AddIndex(
            model_name="journalentry",
            index=models.Index(fields=["legal_entity", "business_unit", "document_date"], name="journal_document_date_idx"),
        ),
    ]
