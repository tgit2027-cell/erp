"""Temporary synthetic files only; no Django, database, services or external tools."""

from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from scripts import backup_artifacts, backup_evidence


NOW = "2026-10-19T12:00:00+00:00"
PRIVATE = "PRIVATE_SYNTHETIC_PASSWORD_OR_PAYLOAD"


class BackupEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="synthetic-backup-evidence-")
        self.addCleanup(self.temporary.cleanup)
        self.bundle = Path(self.temporary.name) / "bundle"
        self.bundle.mkdir()
        self.database = {
            "schema_version": 1, "database_name": "tivoli_erp_demo", "postgres_major": 16,
            "table_rows": {PRIVATE: 2}, "applied_migrations": [PRIVATE],
        }
        self.media = {"schema_version": 1, "included": False, "file_count": 0, "total_bytes": 0}
        self.write("database.dump", b"SYNTHETIC opaque dump, NOT an actual PostgreSQL archive: " + PRIVATE.encode())
        self.write("database-manifest.json", self.database)
        self.write("media-manifest.json", self.media)
        self.hashes()

    def write(self, name, value):
        data = json.dumps(value).encode() if isinstance(value, dict) else value
        (self.bundle / name).write_bytes(data)

    def hashes(self):
        mapping = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.bundle.iterdir() if path.name != "SHA256SUMS.json" and path.is_file()
        }
        self.write("SHA256SUMS.json", {"schema_version": 1, "sha256": mapping})

    def summary(self, **kwargs):
        return backup_evidence.summarize(
            kwargs.pop("path", self.bundle), observed_at=kwargs.pop("observed_at", NOW),
            synthetic=kwargs.pop("synthetic", True), **kwargs,
        )

    def archive(self, members=None):
        members = members or [(PRIVATE, b"SYNTHETIC MEDIA", tarfile.REGTYPE, 0o600)]
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name, content, kind, mode in members:
                member = tarfile.TarInfo(name)
                member.type, member.mode = kind, mode
                member.size = len(content) if kind == tarfile.REGTYPE else 0
                archive.addfile(member, io.BytesIO(content))
        self.write("media.tar.gz", output.getvalue())
        self.media = {"schema_version": 1, "included": True,
                      "file_count": len(members), "total_bytes": sum(len(row[1]) for row in members)}
        self.write("media-manifest.json", self.media)
        self.hashes()

    @staticmethod
    def metadata(directory):
        def exact(info):
            return (tuple(info), info.st_atime_ns, info.st_mtime_ns, info.st_ctime_ns)
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOATIME)
        try:
            with os.scandir(descriptor) as entries:
                names = sorted(entry.name for entry in entries)
            return {
                ".": exact(os.fstat(descriptor)),
                **{name: exact(os.stat(name, dir_fd=descriptor, follow_symlinks=False)) for name in names},
            }
        finally:
            os.close(descriptor)

    def test_complete_no_media_bundle_and_deterministic_observation_metadata(self):
        first = self.summary()
        self.assertEqual(first, self.summary())
        self.assertEqual(first["status"], "artifact_integrity_verified")
        self.assertEqual(first["checksum_verifier"], "verified")
        self.assertEqual(first["media_verifier"], "not_included")
        self.assertEqual(first["restore_recoverability"], "not_established")
        self.assertEqual(first["observed_at"], NOW)
        self.assertTrue(first["timestamps_are_observations_only"])
        self.assertEqual(first["database_metadata"], {
            "schema_version": 1, "postgres_major": 16, "table_count": 1,
            "total_rows": 2, "applied_migration_count": 1, "synthetic_name_recognized": True,
        })
        for row in first["artifacts"].values():
            if row["present"]:
                self.assertIsNotNone(datetime.fromisoformat(row["modified_at_observed"]).tzinfo)
                self.assertNotIn("backup_completed_at", row)

    def test_media_inspection_reuses_verifiers_without_extracting_or_running_restore(self):
        self.archive()
        with patch.object(backup_artifacts, "checksums_verify", wraps=backup_artifacts.checksums_verify) as hashes, \
             patch.object(backup_artifacts, "media_verify", wraps=backup_artifacts.media_verify) as media, \
             patch.object(backup_artifacts, "extract_media", side_effect=AssertionError("No extraction")), \
             patch.object(tarfile.TarFile, "extractall", side_effect=AssertionError("No extraction")):
            result = self.summary()
        self.assertEqual(result["status"], "artifact_integrity_verified")
        self.assertEqual(result["media_verifier"], "verified_read_only")
        self.assertEqual(hashes.call_count, 1)
        self.assertTrue(hashes.call_args.kwargs["quiet"])
        self.assertTrue(media.call_args.kwargs["inspect_only"])

    def test_missing_required_artifacts_and_missing_optional_declared_media(self):
        for name in backup_evidence.REQUIRED:
            with self.subTest(name=name):
                saved = (self.bundle / name).read_bytes()
                (self.bundle / name).unlink()
                result = self.summary()
                self.assertIn("missing_required_artifacts", result["issues"])
                self.assertFalse(result["artifacts"][name]["present"])
                self.write(name, saved)
        self.archive()
        (self.bundle / "media.tar.gz").unlink()
        result = self.summary()
        self.assertIn("missing_media_archive", result["issues"])
        self.assertTrue(result["artifacts"]["media.tar.gz"]["required"])

    def test_malformed_duplicate_or_unsupported_manifests_never_look_verified(self):
        for name in ("SHA256SUMS.json", "database-manifest.json", "media-manifest.json"):
            saved = (self.bundle / name).read_bytes()
            for value in (b"{", b"[]", b'{"schema_version":2}', b'{"schema_version":1,"schema_version":1}',
                          b'{"schema_version":true}', b"\xff"):
                with self.subTest(name=name, value=value):
                    self.write(name, value)
                    self.assertNotEqual(self.summary()["status"], "artifact_integrity_verified")
            self.write(name, saved)

    def test_tampered_dump_or_media_is_not_integrity_or_recoverability_success(self):
        self.write("database.dump", b"TAMPERED " + PRIVATE.encode())
        result = self.summary()
        self.assertEqual(result["checksum_verifier"], "mismatch")
        self.assertIn("checksum_mismatch", result["issues"])
        self.assertEqual(result["restore_recoverability"], "not_established")
        self.archive()
        self.write("media.tar.gz", b"NOT A GZIP " + PRIVATE.encode())
        result = self.summary()
        self.assertIn("checksum_mismatch", result["issues"])
        self.assertIn("invalid_or_unsafe_media_archive", result["issues"])

    def test_incomplete_checksums_and_unexpected_manifest_entries_are_rejected(self):
        base = json.loads((self.bundle / "SHA256SUMS.json").read_text())
        for key in ("../" + PRIVATE, "/" + PRIVATE, "sub/" + PRIVATE, PRIVATE):
            value = json.loads(json.dumps(base))
            value["sha256"][key] = "0" * 64
            self.write("SHA256SUMS.json", value)
            self.assertIn("incomplete_or_unexpected_checksum_entries", self.summary()["issues"])
        base["sha256"].pop("database.dump")
        self.write("SHA256SUMS.json", base)
        self.assertIn("incomplete_or_unexpected_checksum_entries", self.summary()["issues"])
        self.write("database-manifest.json", {**self.database, PRIVATE: PRIVATE})
        self.assertIn("unexpected_database_manifest_entries", self.summary()["issues"])

    def test_reject_non_synthetic_manifest_attestation_and_invalid_observation_time(self):
        self.assertIn("synthetic_attestation_required", self.summary(synthetic=False)["issues"])
        self.write("database-manifest.json", {**self.database, "database_name": PRIVATE})
        self.assertIn("non_synthetic_database_manifest", self.summary()["issues"])
        for value in ("not-a-date", "2026-10-19", "2026-10-19T12:00:00"):
            self.assertIn("invalid_observation_time", self.summary(observed_at=value)["issues"])

    def test_symlink_ancestors_artifacts_hardlinks_special_files_and_parent_paths_rejected(self):
        alias = Path(self.temporary.name) / "alias"
        alias.symlink_to(self.bundle, target_is_directory=True)
        self.assertNotEqual(self.summary(path=alias)["status"], "artifact_integrity_verified")
        child = self.bundle / "child"
        child.mkdir()
        self.assertNotEqual(self.summary(path=alias / "child")["status"], "artifact_integrity_verified")
        child.rmdir()
        self.assertIn("unsafe_bundle_path", self.summary(path=self.bundle / ".." / "bundle")["issues"])
        self.assertIn("unsafe_bundle_path", self.summary(path="/")["issues"])
        original = self.bundle / "database.dump"
        saved = original.read_bytes()
        original.unlink()
        external = Path(self.temporary.name) / PRIVATE
        external.write_bytes(saved)
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    original.symlink_to(external)
                elif kind == "hardlink":
                    os.link(external, original)
                else:
                    os.mkfifo(original)
                self.assertNotEqual(self.summary()["status"], "artifact_integrity_verified")
                original.unlink()
        self.write("database.dump", saved)

    def test_unsafe_archive_members_and_metadata_mismatch_are_rejected_without_extraction(self):
        cases = [
            [("../" + PRIVATE, b"x", tarfile.REGTYPE, 0o600)],
            [("/" + PRIVATE, b"x", tarfile.REGTYPE, 0o600)],
            [(PRIVATE, b"", tarfile.SYMTYPE, 0o600)],
            [(PRIVATE, b"x", tarfile.REGTYPE, 0o4600)],
            [(PRIVATE, b"x", tarfile.REGTYPE, 0o600)] * 2,
            [("folder\\" + PRIVATE, b"x", tarfile.REGTYPE, 0o600)],
            [("./" + PRIVATE, b"x", tarfile.REGTYPE, 0o600)],
            [("a", b"x", tarfile.REGTYPE, 0o600), ("a/b", b"x", tarfile.REGTYPE, 0o600)],
        ]
        for members in cases:
            with self.subTest(members=members):
                self.archive(members)
                self.assertIn("invalid_or_unsafe_media_archive", self.summary()["issues"])
        self.archive()
        self.write("media-manifest.json", {**self.media, "file_count": 2})
        self.hashes()
        self.assertIn("invalid_or_unsafe_media_archive", self.summary()["issues"])

    def test_corrupt_gzip_trailer_is_not_verified_even_with_updated_checksums(self):
        self.archive()
        raw = bytearray((self.bundle / "media.tar.gz").read_bytes())
        raw[-8] ^= 255  # CRC, not tar headers/file contents.
        self.write("media.tar.gz", bytes(raw))
        self.hashes()
        result = self.summary()
        self.assertEqual(result["checksum_verifier"], "verified")
        self.assertEqual(result["media_verifier"], "invalid")
        self.assertIn("invalid_or_unsafe_media_archive", result["issues"])

    def test_empty_dump_inconsistent_media_and_extra_files_are_incomplete(self):
        self.write("database.dump", b"")
        self.hashes()
        self.assertIn("empty_database_dump", self.summary()["issues"])
        self.write("media.tar.gz", b"unexpected")
        self.assertIn("unexpected_media_archive", self.summary()["issues"])
        self.write(PRIVATE, b"credentials must not be echoed")
        result = self.summary()
        self.assertIn("unexpected_bundle_entries", result["issues"])
        self.assertNotIn(PRIVATE, json.dumps(result))

    def test_redaction_in_success_errors_and_cli_output(self):
        for result in (self.summary(), self.summary(path=Path(self.temporary.name) / PRIVATE)):
            text = json.dumps(result)
            self.assertNotIn(PRIVATE, text)
            self.assertNotIn(self.temporary.name, text)
        output = io.StringIO()
        with redirect_stdout(output):
            code = backup_evidence.main([
                "--bundle", str(self.bundle), "--synthetic", "--observed-at", NOW,
            ])
        self.assertEqual(code, 0)
        self.assertNotIn(PRIVATE, output.getvalue())
        output = io.StringIO()
        with redirect_stdout(output):
            code = backup_evidence.main([
                "--bundle", str(self.bundle / PRIVATE), "--synthetic", "--observed-at", NOW,
            ])
        self.assertEqual(code, 1)
        self.assertNotIn(PRIVATE, output.getvalue())

    def test_no_mutations_database_subprocess_network_or_extraction_including_atime(self):
        self.archive()
        before = self.metadata(self.bundle)
        real_open = os.open

        def readonly_open(path, flags, *args, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            return real_open(path, flags, *args, **kwargs)

        with ExitStack() as stack:
            for name in ("mkdir", "makedirs", "unlink", "remove", "rmdir", "rename", "replace",
                         "chmod", "chown", "utime", "symlink", "link"):
                stack.enter_context(patch.object(os, name, side_effect=AssertionError("No mutation")))
            stack.enter_context(patch.object(os, "open", side_effect=readonly_open))
            stack.enter_context(patch.object(os, "system", side_effect=AssertionError("No subprocess")))
            stack.enter_context(patch.object(os, "fork", side_effect=AssertionError("No subprocess")))
            stack.enter_context(patch.object(os, "posix_spawn", side_effect=AssertionError("No subprocess")))
            stack.enter_context(patch.object(subprocess, "Popen", side_effect=AssertionError("No subprocess")))
            stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("No network")))
            stack.enter_context(patch.object(tempfile, "TemporaryDirectory", side_effect=AssertionError("No temp output")))
            stack.enter_context(patch.object(tarfile.TarFile, "extract", side_effect=AssertionError("No extraction")))
            stack.enter_context(patch.object(tarfile.TarFile, "extractall", side_effect=AssertionError("No extraction")))
            # The reader has no ORM or database-driver imports, including lazy ones.
            import builtins
            original_import = builtins.__import__

            def no_database(name, *args, **kwargs):
                if name.split(".")[0] in {"django", "psycopg", "psycopg2", "sqlite3"}:
                    raise AssertionError("No database imports or connections")
                return original_import(name, *args, **kwargs)
            stack.enter_context(patch.object(builtins, "__import__", side_effect=no_database))
            self.assertEqual(self.summary()["status"], "artifact_integrity_verified")
        self.assertEqual(before, self.metadata(self.bundle))

    def test_permission_or_noatime_failure_is_redacted_and_never_falls_back(self):
        with patch.object(os, "open", side_effect=PermissionError(PRIVATE)):
            result = self.summary()
        self.assertEqual(result["issues"], ["unreadable_or_unsafe_local_evidence"])
        self.assertNotIn(PRIVATE, json.dumps(result))

    def test_concurrent_artifact_replacement_is_rejected(self):
        original = backup_artifacts.checksums_verify

        def replace_before_hash(*args, **kwargs):
            self.write("database.dump", b"CHANGED")
            return original(*args, **kwargs)

        with patch.object(backup_artifacts, "checksums_verify", side_effect=replace_before_hash):
            result = self.summary()
        self.assertIn("artifact_changed_during_read", result["issues"])
        self.assertNotEqual(result["status"], "artifact_integrity_verified")

    def test_legacy_checksum_and_no_media_verifier_entrypoints_remain_compatible(self):
        output = io.StringIO()
        with redirect_stdout(output):
            backup_artifacts.checksums_verify(self.bundle)
            backup_artifacts.media_verify(self.bundle)
        self.assertIn("Backup checksums verified", output.getvalue())
        self.assertIn("no media files were present", output.getvalue())


if __name__ == "__main__":
    unittest.main()
