from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("foundation", "0024_finance_draft_revisions")]
    operations = [
        migrations.AddField(
            model_name="financedraftjournal", name="kind",
            field=models.CharField(choices=[("journal", "Journal draft"), ("recurring_template", "Recurring template proposal")], default="journal", max_length=24),
        ),
        migrations.AddField(
            model_name="financedraftjournal", name="recurrence_proposal",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddConstraint(
            model_name="financedraftjournal",
            constraint=models.CheckConstraint(
                condition=models.Q(kind="journal", recurrence_proposal={}) | (models.Q(kind="recurring_template") & ~models.Q(recurrence_proposal={})),
                name="finance_draft_proposal_kind",
            ),
        ),
    ]
