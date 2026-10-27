from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations

# Substring search (Django's icontains compares UPPER(column) LIKE UPPER(term))
# must stay fast on catalogs of tens of thousands of items, so each searched
# column gets a trigram index on its upper-cased value.
SEARCHED_COLUMNS = (
    ("foundation_item", "item_code", "item_item_code_trgm"),
    ("foundation_item", "description", "item_description_trgm"),
    ("foundation_item", "name_en", "item_name_en_trgm"),
    ("foundation_item", "name_ar", "item_name_ar_trgm"),
    ("foundation_variant", "sku", "variant_sku_trgm"),
    ("foundation_variant", "description", "variant_description_trgm"),
    ("foundation_syntheticcounterparty", "code", "party_code_trgm"),
    ("foundation_syntheticcounterparty", "name", "party_name_trgm"),
    ("foundation_syntheticcounterparty", "name_ar", "party_name_ar_trgm"),
    ("foundation_syntheticcounterparty", "contact_en", "party_contact_en_trgm"),
)


class Migration(migrations.Migration):

    dependencies = [
        ("foundation", "0037_audit_log_favorite"),
    ]

    operations = [
        # pg_trgm is a trusted extension (PostgreSQL 13+), so the database owner can enable it.
        TrigramExtension(),
        *[
            migrations.RunSQL(
                f'CREATE INDEX "{name}" ON "{table}" USING gin ((UPPER("{column}"::text)) gin_trgm_ops);',
                reverse_sql=f'DROP INDEX IF EXISTS "{name}";',
            )
            for table, column, name in SEARCHED_COLUMNS
        ],
    ]
