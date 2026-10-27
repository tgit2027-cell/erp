#!/usr/bin/env python3
"""Create and verify checksums and optional uploaded-media backup artifacts."""

import argparse
import gzip
import hashlib
import json
import stat
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


def regular_files(root: Path):
    files = []
    if not root.exists():
        return files
    if not root.is_dir():
        raise SystemExit(f"Media root is not a directory: {root}")
    for path in root.rglob("*"):
        if path.is_symlink():
            raise SystemExit("Media backup refuses symbolic links.")
        if path.is_file():
            files.append(path)
    return sorted(files)


def sha256(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def media_write(root: Path, bundle: Path):
    root = root.expanduser().resolve()
    bundle.mkdir(parents=True, exist_ok=True)
    files = regular_files(root)
    manifest = {
        "schema_version": 1,
        "included": bool(files),
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
    }
    (bundle / "media-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    archive = bundle / "media.tar.gz"
    if files:
        with tarfile.open(archive, "w:gz") as tar:
            for path in files:
                def private_file_mode(member):
                    member.mode = 0o600
                    return member

                tar.add(
                    path,
                    arcname=path.relative_to(root).as_posix(),
                    recursive=False,
                    filter=private_file_mode,
                )
    elif archive.exists():
        archive.unlink()
    print(
        "Media snapshot: "
        + (f"{len(files)} file(s), {manifest['total_bytes']} bytes." if files else "no files present.")
    )


def extract_media(bundle: Path, root: Path):
    bundle = bundle.expanduser().resolve()
    root = root.expanduser().resolve()
    try:
        manifest = json.loads((bundle / "media-manifest.json").read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"Cannot read media manifest: {error}") from error
    if manifest.get("schema_version") != 1:
        raise SystemExit("Unsupported media manifest version.")
    archive = bundle / "media.tar.gz"
    if not manifest.get("included"):
        if archive.exists():
            raise SystemExit("Media archive exists although the manifest says no media was included.")
        return manifest
    if not archive.is_file():
        raise SystemExit("Media archive is missing.")
    if root.exists() and any(root.iterdir()):
        raise SystemExit(f"Refusing to extract media into a non-empty directory: {root}")
    root.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts or not member.isfile():
                    raise SystemExit("Media archive contains an unsafe or unsupported path.")
                if member.mode & (stat.S_ISUID | stat.S_ISGID):
                    raise SystemExit("Media archive contains unsafe permission bits.")
            tar.extractall(root, members=members, filter="data")
        files = regular_files(root)
        actual_count = len(files)
        actual_bytes = sum(path.stat().st_size for path in files)
    except Exception:
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                path.rmdir()
        root.rmdir()
        raise
    if actual_count != manifest.get("file_count") or actual_bytes != manifest.get("total_bytes"):
        raise SystemExit(
            f"Media restore verification failed: expected {manifest.get('file_count')} file(s) / "
            f"{manifest.get('total_bytes')} bytes; restored {actual_count} file(s) / {actual_bytes} bytes."
        )
    return manifest


def inspect_media_archive(source, manifest):
    """Inspect archive bytes without extraction; also reusable by read-only tools."""
    count = total = 0
    seen = set()
    with gzip.GzipFile(fileobj=source, mode="rb") as uncompressed, \
            tarfile.open(fileobj=uncompressed, mode="r|") as tar:
        for member in tar:
            name = PurePosixPath(member.name)
            if (
                name.is_absolute() or ".." in name.parts or not member.isfile()
                or "\\" in member.name or member.name in seen
                or name.as_posix() != member.name or member.name in {"", "."}
                or any(parent.as_posix() in seen for parent in name.parents if parent.as_posix() != ".")
                or any(previous.startswith(member.name + "/") for previous in seen)
                or member.mode & (stat.S_ISUID | stat.S_ISGID)
            ):
                raise SystemExit("Media archive contains an unsafe or unsupported path.")
            seen.add(member.name)
            count += 1
            total += member.size
            if count > manifest["file_count"] or total > manifest["total_bytes"]:
                raise SystemExit("Media archive exceeds its declared counts.")
            # Consume the stream, checking readable file content without saving it.
            with tar.extractfile(member) as content:
                read = 0
                for block in iter(lambda: content.read(1024 * 1024), b""):
                    read += len(block)
                if read != member.size:
                    raise SystemExit("Media archive content is incomplete.")
        # tar end markers can precede the gzip trailer. Consume it to validate
        # the compressed stream's integrity without extracting anything.
        for _block in iter(lambda: uncompressed.read(1024 * 1024), b""):
            pass
    if count != manifest["file_count"] or total != manifest["total_bytes"]:
        raise SystemExit("Media archive counts do not match its manifest.")
    return manifest


def media_verify(bundle: Path, *, inspect_only=False, opener=None, manifest=None):
    if inspect_only:
        if opener is None or manifest is None:
            raise ValueError("Read-only media verification requires an explicit reader and manifest.")
        if manifest["included"]:
            with opener("media.tar.gz") as source:
                return inspect_media_archive(source, manifest)
        return manifest
    with tempfile.TemporaryDirectory(prefix="tivoli-media-restore-") as temporary:
        manifest = extract_media(bundle, Path(temporary) / "media")
    if manifest.get("included"):
        print(
            f"Media restore verified: {manifest['file_count']} file(s), "
            f"{manifest['total_bytes']} bytes."
        )
    else:
        print("Media restore verified: no media files were present in the source.")


def media_extract(bundle: Path, destination: Path):
    manifest = extract_media(bundle, destination)
    if manifest.get("included"):
        print(
            f"Media restored and verified: {manifest['file_count']} file(s), "
            f"{manifest['total_bytes']} bytes at {destination.expanduser().resolve()}."
        )
    else:
        print("No media files were present; no destination directory was created.")


def checksums_write(bundle: Path):
    bundle = bundle.expanduser().resolve()
    names = ["database.dump", "database-manifest.json", "media-manifest.json", "media.tar.gz"]
    checksums = {
        name: sha256(bundle / name)
        for name in names
        if (bundle / name).is_file()
    }
    if "database.dump" not in checksums or "database-manifest.json" not in checksums:
        raise SystemExit("Database dump and integrity manifest are required for a backup bundle.")
    (bundle / "SHA256SUMS.json").write_text(
        json.dumps({"schema_version": 1, "sha256": checksums}, indent=2, sort_keys=True) + "\n"
    )
    print(f"Checksums written for {len(checksums)} backup artifact(s).")


def checksums_verify(bundle: Path, *, data=None, digest_for=None, quiet=False):
    if digest_for is None:
        bundle = bundle.expanduser().resolve()
    if data is None:
        try:
            data = json.loads((bundle / "SHA256SUMS.json").read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise SystemExit(f"Cannot read checksum manifest: {error}") from error
    if data.get("schema_version") != 1 or not isinstance(data.get("sha256"), dict):
        raise SystemExit("Unsupported checksum manifest.")
    for name, expected in data["sha256"].items():
        if digest_for is not None:
            if digest_for(name) != expected:
                raise SystemExit("Backup checksum verification failed.")
            continue
        path = bundle / name
        if path.parent != bundle or not path.is_file() or sha256(path) != expected:
            raise SystemExit(f"Backup checksum verification failed for {name}.")
    if not quiet:
        print(f"Backup checksums verified: {len(data['sha256'])} artifact(s).")


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    media = subparsers.add_parser("media-write")
    media.add_argument("--root", type=Path, required=True)
    media.add_argument("--bundle", type=Path, required=True)
    verify_media = subparsers.add_parser("media-verify")
    verify_media.add_argument("--bundle", type=Path, required=True)
    extract_media_parser = subparsers.add_parser("media-extract")
    extract_media_parser.add_argument("--bundle", type=Path, required=True)
    extract_media_parser.add_argument("--destination", type=Path, required=True)
    write_hashes = subparsers.add_parser("checksums-write")
    write_hashes.add_argument("--bundle", type=Path, required=True)
    verify_hashes = subparsers.add_parser("checksums-verify")
    verify_hashes.add_argument("--bundle", type=Path, required=True)
    options = parser.parse_args()
    if options.command == "media-write":
        media_write(options.root, options.bundle)
    elif options.command == "media-verify":
        media_verify(options.bundle)
    elif options.command == "media-extract":
        media_extract(options.bundle, options.destination)
    elif options.command == "checksums-write":
        checksums_write(options.bundle)
    else:
        checksums_verify(options.bundle)


if __name__ == "__main__":
    main()
