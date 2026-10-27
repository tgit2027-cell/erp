import importlib.util
import json
import tempfile
from pathlib import Path

from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import FileField
from django.test import TestCase, override_settings


class MediaStorageInventoryTests(TestCase):
    def test_only_supplier_kyc_documents_store_uploaded_files(self):
        file_fields = [
            f"{model.__name__}.{field.name}"
            for model in apps.get_app_config("foundation").get_models()
            for field in model._meta.get_fields()
            if isinstance(field, FileField)
        ]
        # Supplier KYC attachments are the only uploads; they live under DJANGO_MEDIA_ROOT
        # and are covered by the media archive in the backup bundle.
        self.assertEqual(file_fields, ["SupplierKycDocument.file"])


class SnapshotIntegrityCommandTests(TestCase):
    def test_manifest_round_trip_compares_row_counts_and_migrations(self):
        with tempfile.TemporaryDirectory(prefix="tivoli-integrity-") as directory:
            manifest = Path(directory) / "manifest.json"
            call_command("snapshot_integrity", output=str(manifest), verbosity=0)
            data = json.loads(manifest.read_text())
            self.assertEqual(data["schema_version"], 1)
            self.assertTrue(data["table_rows"])
            self.assertTrue(data["applied_migrations"])
            self.assertEqual(data["postgres_major"], 16)
            call_command("snapshot_integrity", compare=str(manifest), verbosity=0)

    def test_manifest_refuses_non_demo_environment(self):
        with tempfile.TemporaryDirectory(prefix="tivoli-integrity-") as directory:
            with override_settings(APP_ENV="production"):
                with self.assertRaisesRegex(CommandError, "APP_ENV=demo"):
                    call_command(
                        "snapshot_integrity",
                        output=str(Path(directory) / "manifest.json"),
                        verbosity=0,
                    )

    def test_load_harness_refuses_the_development_database(self):
        with self.assertRaisesRegex(CommandError, "separate database"):
            call_command("synthetic_load_baseline", verbosity=0)


class BackupMediaUtilityTests(TestCase):
    @staticmethod
    def _load_utility():
        script = Path(__file__).resolve().parents[1] / "scripts" / "backup_artifacts.py"
        spec = importlib.util.spec_from_file_location("tivoli_backup_artifacts", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_media_bundle_checksum_and_clean_extraction_round_trip(self):
        utility = self._load_utility()
        with tempfile.TemporaryDirectory(prefix="tivoli-media-") as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "scan.dat").write_bytes(b"synthetic attachment")
            (source / "nested").mkdir()
            (source / "nested" / "note.txt").write_text("synthetic only")
            bundle = root / "bundle"
            bundle.mkdir()
            (bundle / "database.dump").write_bytes(b"test-only-dump")
            (bundle / "database-manifest.json").write_text("{}\n")
            utility.media_write(source, bundle)
            utility.checksums_write(bundle)
            utility.checksums_verify(bundle)
            utility.media_verify(bundle)
            restored = root / "restored"
            utility.media_extract(bundle, restored)
            self.assertEqual((restored / "scan.dat").read_bytes(), b"synthetic attachment")
            self.assertEqual((restored / "scan.dat").stat().st_mode & 0o777, 0o600)
            self.assertEqual((restored / "nested" / "note.txt").read_text(), "synthetic only")

    def test_media_restore_refuses_to_overwrite_existing_content(self):
        utility = self._load_utility()
        with tempfile.TemporaryDirectory(prefix="tivoli-media-") as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "file.txt").write_text("synthetic")
            bundle = root / "bundle"
            bundle.mkdir()
            utility.media_write(source, bundle)
            destination = root / "destination"
            destination.mkdir()
            existing = destination / "keep.txt"
            existing.write_text("keep")
            with self.assertRaisesRegex(SystemExit, "non-empty directory"):
                utility.media_extract(bundle, destination)
            self.assertEqual(existing.read_text(), "keep")
