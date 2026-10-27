#!/usr/bin/env python3
"""Read-only synthetic backup evidence; stdlib only, no database or extraction."""

import sys

# The standalone inspector must not create import caches alongside source files.
sys.dont_write_bytecode = True

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat

try:
    from . import backup_artifacts
except ImportError:  # Direct script execution.
    import backup_artifacts


NAMES = ("database.dump", "database-manifest.json", "media-manifest.json",
         "media.tar.gz", "SHA256SUMS.json")
REQUIRED = frozenset(NAMES) - {"media.tar.gz"}
MAX_MANIFEST_BYTES = 1024 * 1024  # Technical parser bound, not retention policy.


class EvidenceError(Exception):
    pass


def utc(value):
    return value.astimezone(timezone.utc).isoformat()


def observation_time(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError
        return utc(parsed)
    except (ValueError, TypeError):
        raise EvidenceError("invalid_observation_time") from None


def signature(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


class BundleReader:
    """Pinned directory descriptor; refuse links/special files and atime writes."""

    def __init__(self, path):
        self.fd = None
        if not all(hasattr(os, flag) for flag in ("O_NOATIME", "O_NOFOLLOW", "O_DIRECTORY")):
            raise EvidenceError("read_only_platform_not_supported")
        path = Path(path).expanduser()
        if ".." in path.parts or str(path).startswith("//"):
            raise EvidenceError("unsafe_bundle_path")
        path = path.absolute()
        if len(path.parts) < 2:
            raise EvidenceError("unsafe_bundle_path")
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        try:
            for index, part in enumerate(path.parts[1:], start=1):
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                if index == len(path.parts) - 1:
                    flags |= os.O_NOATIME
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            self.fd = descriptor
            self.initial = os.fstat(self.fd)
            with os.scandir(self.fd) as entries:
                self.names = frozenset(entry.name for entry in entries)
            self.stats = {}
            for name in self.names:
                info = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
                if name not in NAMES:
                    raise EvidenceError("unexpected_bundle_entries")
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise EvidenceError("unsafe_artifact_type_or_link")
                self.stats[name] = info
        except BaseException:
            os.close(descriptor)
            self.fd = None
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    @contextmanager
    def open(self, name):
        if name not in self.stats:
            raise EvidenceError("missing_artifact")
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME,
            dir_fd=self.fd,
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or signature(info) != signature(self.stats[name]):
                raise EvidenceError("artifact_changed_during_read")
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                yield source
                if signature(os.fstat(descriptor)) != signature(info):
                    raise EvidenceError("artifact_changed_during_read")
        finally:
            os.close(descriptor)

    def digest(self, name):
        digest = hashlib.sha256()
        with self.open(name) as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def manifest(self, name):
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError
                result[key] = value
            return result
        with self.open(name) as source:
            data = source.read(MAX_MANIFEST_BYTES + 1)
        if len(data) > MAX_MANIFEST_BYTES:
            raise EvidenceError("manifest_too_large")
        try:
            value = json.loads(data, object_pairs_hook=unique)
        except (ValueError, UnicodeError):
            raise EvidenceError("malformed_manifest") from None
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int \
                or value["schema_version"] != 1:
            raise EvidenceError("unsupported_manifest_schema")
        return value

    def unchanged(self):
        if signature(os.fstat(self.fd)) != signature(self.initial):
            raise EvidenceError("bundle_changed_during_read")
        with os.scandir(self.fd) as entries:
            if frozenset(entry.name for entry in entries) != self.names:
                raise EvidenceError("bundle_changed_during_read")
        for name, before in self.stats.items():
            after = os.stat(name, dir_fd=self.fd, follow_symlinks=False)
            if signature(before) != signature(after):
                raise EvidenceError("artifact_changed_during_read")


def natural(value):
    return type(value) is int and value >= 0


def database_metadata(data):
    if set(data) != {"schema_version", "database_name", "postgres_major", "table_rows", "applied_migrations"}:
        raise EvidenceError("unexpected_database_manifest_entries")
    name = data["database_name"]
    if not isinstance(name, str) or not (
        name in {"tivoli_erp_demo", "test_tivoli_erp_demo"} or name.startswith("tivoli_erp_restore_test_")
    ):
        raise EvidenceError("non_synthetic_database_manifest")
    rows, migrations = data["table_rows"], data["applied_migrations"]
    if not natural(data["postgres_major"]) or data["postgres_major"] == 0 \
            or not isinstance(rows, dict) or not all(natural(value) for value in rows.values()) \
            or not isinstance(migrations, list) or not all(isinstance(item, str) for item in migrations) \
            or len(set(migrations)) != len(migrations):
        raise EvidenceError("malformed_database_manifest")
    return {"schema_version": 1, "postgres_major": data["postgres_major"],
            "table_count": len(rows), "total_rows": sum(rows.values()),
            "applied_migration_count": len(migrations), "synthetic_name_recognized": True}


def media_metadata(data):
    if set(data) != {"schema_version", "included", "file_count", "total_bytes"} \
            or type(data["included"]) is not bool \
            or not natural(data["file_count"]) or not natural(data["total_bytes"]) \
            or data["included"] != (data["file_count"] > 0) \
            or (not data["included"] and data["total_bytes"] != 0):
        raise EvidenceError("malformed_media_manifest")
    return data


def summarize(path, *, observed_at, synthetic=False):
    result = {
        "label": "SYNTHETIC BACKUP EVIDENCE ONLY — artifact integrity does not establish restore recoverability.",
        "read_only": True, "restore_recoverability": "not_established",
        "timestamps_are_observations_only": True, "status": "incomplete_or_invalid",
        "artifacts": {}, "database_metadata": None, "media_metadata": None,
        "checksum_verifier": "not_run", "media_verifier": "not_run", "issues": [],
    }
    reader = None
    try:
        result["observed_at"] = observation_time(observed_at)
        if not synthetic:
            raise EvidenceError("synthetic_attestation_required")
        reader = BundleReader(path)
        for name in NAMES:
            info = reader.stats.get(name)
            result["artifacts"][name] = {
                "present": info is not None, "required": name in REQUIRED,
                "size_bytes": info.st_size if info else None,
                "modified_at_observed": utc(datetime.fromtimestamp(info.st_mtime, timezone.utc)) if info else None,
                "metadata_changed_at_observed": utc(datetime.fromtimestamp(info.st_ctime, timezone.utc)) if info else None,
            }
        missing = REQUIRED - reader.names
        if missing:
            result["issues"].append("missing_required_artifacts")

        def load(name, validate, key):
            if name not in reader.names:
                return None
            try:
                data = validate(reader.manifest(name))
                result[key] = data
                return data
            except EvidenceError as error:
                result["issues"].append(str(error))
                return None

        database = load("database-manifest.json", database_metadata, "database_metadata")
        media = load("media-manifest.json", media_metadata, "media_metadata")
        if media and media["included"]:
            result["artifacts"]["media.tar.gz"]["required"] = True
            if "media.tar.gz" not in reader.names:
                result["issues"].append("missing_media_archive")
        if media and not media["included"] and "media.tar.gz" in reader.names:
            result["issues"].append("unexpected_media_archive")
        if "database.dump" in reader.names and reader.stats["database.dump"].st_size == 0:
            result["issues"].append("empty_database_dump")

        if "SHA256SUMS.json" in reader.names:
            try:
                hashes = reader.manifest("SHA256SUMS.json")
                expected = reader.names - {"SHA256SUMS.json"}
                mapping = hashes.get("sha256")
                if set(hashes) != {"schema_version", "sha256"} or not isinstance(mapping, dict) \
                        or set(mapping) != expected or not expected.issubset(set(NAMES) - {"SHA256SUMS.json"}) \
                        or not all(isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value)
                                   for value in mapping.values()):
                    raise EvidenceError("incomplete_or_unexpected_checksum_entries")
                if not missing and database is not None and media is not None:
                    try:
                        backup_artifacts.checksums_verify(
                            Path("."), data=hashes, digest_for=reader.digest, quiet=True,
                        )
                    except SystemExit:
                        result["checksum_verifier"] = "mismatch"
                        result["issues"].append("checksum_mismatch")
                    else:
                        result["checksum_verifier"] = "verified"
            except EvidenceError as error:
                result["issues"].append(str(error))

        if media and not any(issue in result["issues"] for issue in
                             ("missing_media_archive", "unexpected_media_archive")):
            try:
                backup_artifacts.media_verify(
                    Path("."), inspect_only=True, opener=reader.open, manifest=media,
                )
                result["media_verifier"] = "verified_read_only" if media["included"] else "not_included"
            except (SystemExit, OSError, ValueError, EOFError, backup_artifacts.tarfile.TarError):
                result["media_verifier"] = "invalid"
                result["issues"].append("invalid_or_unsafe_media_archive")
        reader.unchanged()
        if not result["issues"] and result["checksum_verifier"] == "verified":
            result["status"] = "artifact_integrity_verified"
    except EvidenceError as error:
        result["issues"].append(str(error))
    except (OSError, ValueError, OverflowError):
        # Never echo paths, permission error strings, manifest or payload content.
        result["issues"].append("unreadable_or_unsafe_local_evidence")
    finally:
        if reader:
            reader.close()
    result["issues"] = sorted(set(result["issues"]))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--synthetic", action="store_true", help="Attest that input is synthetic only.")
    parser.add_argument("--observed-at", default=datetime.now(timezone.utc).isoformat())
    args = parser.parse_args(argv)
    result = summarize(args.bundle, observed_at=args.observed_at, synthetic=args.synthetic)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "artifact_integrity_verified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
