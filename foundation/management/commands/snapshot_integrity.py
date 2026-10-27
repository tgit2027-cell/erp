import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder


class Command(BaseCommand):
    help = "Write or compare a local synthetic-demo database integrity manifest."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--output", help="Write a row-count/schema manifest to this path.")
        group.add_argument("--compare", help="Compare this database against an existing manifest.")

    def handle(self, *args, **options):
        self._require_local_demo_database()
        actual = self._manifest()
        if options["output"]:
            output = Path(options["output"]).expanduser()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n")
            self.stdout.write(
                self.style.SUCCESS(
                    f"Integrity manifest written: {len(actual['table_rows'])} tables, "
                    f"{len(actual['applied_migrations'])} applied migrations."
                )
            )
            return

        manifest_path = Path(options["compare"]).expanduser()
        try:
            expected = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise CommandError(f"Cannot read the source integrity manifest: {error}") from error
        differences = []
        if expected.get("schema_version") != actual["schema_version"]:
            differences.append("manifest schema version differs")
        if expected.get("postgres_major") != actual["postgres_major"]:
            differences.append(
                f"PostgreSQL major differs: source={expected.get('postgres_major')} "
                f"restore={actual['postgres_major']}"
            )
        if expected.get("table_rows") != actual["table_rows"]:
            expected_rows = expected.get("table_rows", {})
            actual_rows = actual["table_rows"]
            for table in sorted(set(expected_rows) | set(actual_rows)):
                if expected_rows.get(table) != actual_rows.get(table):
                    differences.append(
                        f"{table} row count differs: source={expected_rows.get(table)} "
                        f"restore={actual_rows.get(table)}"
                    )
        if expected.get("applied_migrations") != actual["applied_migrations"]:
            differences.append("applied migration set differs")
        if differences:
            raise CommandError("Restore integrity verification failed: " + "; ".join(differences))
        self.stdout.write(
            self.style.SUCCESS(
                f"Restore integrity verified: {len(actual['table_rows'])} table row counts and "
                f"{len(actual['applied_migrations'])} migrations match PostgreSQL "
                f"{actual['postgres_major']}."
            )
        )

    @staticmethod
    def _require_local_demo_database():
        if settings.APP_ENV != "demo":
            raise CommandError("Refusing to inspect a database unless APP_ENV=demo.")
        if connection.vendor != "postgresql":
            raise CommandError("Integrity snapshots require PostgreSQL.")
        database = connection.settings_dict.get("NAME", "")
        allowed = database in {"tivoli_erp_demo", "test_tivoli_erp_demo"} or database.startswith(
            "tivoli_erp_restore_test_"
        )
        if not allowed:
            raise CommandError("Refusing an unrecognized database; expected a local synthetic demo/restore database.")
        if connection.settings_dict.get("HOST", "") not in {"", "localhost", "127.0.0.1", "::1"}:
            raise CommandError("Refusing a non-local database.")

    @staticmethod
    def _manifest():
        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version_num")
            version_number = int(cursor.fetchone()[0])
        tables = connection.introspection.table_names()
        counts = {}
        with connection.cursor() as cursor:
            for table in sorted(tables):
                cursor.execute(
                    f"SELECT COUNT(*) FROM {connection.ops.quote_name(table)}"
                )
                counts[table] = int(cursor.fetchone()[0])
        applied = MigrationRecorder(connection).applied_migrations()
        return {
            "schema_version": 1,
            "database_name": connection.settings_dict["NAME"],
            "postgres_major": version_number // 10000,
            "table_rows": counts,
            "applied_migrations": sorted(f"{app}.{name}" for app, name in applied),
        }
