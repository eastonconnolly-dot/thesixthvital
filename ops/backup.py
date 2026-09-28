"""Nightly database backup: dump -> gzip -> upload.

Usage:
    flask backup-db                     # see register_cli() in api/app.py
    from ops.backup import run_backup

Postgres: shells out to `pg_dump` (must be on PATH) and gzips its output.
SQLite (local dev, DATABASE_URL unset): pg_dump doesn't apply, so this just
gzip-copies the .db file instead -- same downstream upload path either way.

Upload target is whichever of these is configured (checked in this order):
  1. A private GitHub release, via the `gh` CLI (BACKUP_GITHUB_REPO env var;
     `gh` must be installed and already `gh auth login`'d wherever this runs)
  2. An S3-compatible bucket, via boto3 (AWS_ACCESS_KEY_ID /
     AWS_SECRET_ACCESS_KEY / BACKUP_BUCKET env vars; BACKUP_S3_ENDPOINT_URL
     for a non-AWS S3-compatible host like R2 or Backblaze)

run_backup() raises a clear, actionable RuntimeError if neither is set up,
rather than silently doing nothing.
"""

import gzip
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

from flask import current_app


def _timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _is_sqlite(database_url):
    return database_url.startswith("sqlite:")


def _sqlite_path(database_url):
    # sqlite:///relative/path.db or sqlite:////absolute/path.db
    return database_url.split("sqlite:///", 1)[1]


def dump_database(dest_dir):
    """Writes a gzip-compressed dump into dest_dir and returns its path.
    Format-agnostic on purpose so the upload step doesn't care which branch
    produced the file."""
    database_url = current_app.config["SQLALCHEMY_DATABASE_URI"]
    ts = _timestamp()

    if _is_sqlite(database_url):
        src = _sqlite_path(database_url)
        if not os.path.exists(src):
            raise RuntimeError(f"SQLite database file not found at {src!r} -- nothing to back up.")
        dest = os.path.join(dest_dir, f"rpsas-backup-{ts}.db.gz")
        with open(src, "rb") as f_in, gzip.open(dest, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        return dest

    if shutil.which("pg_dump") is None:
        raise RuntimeError(
            "pg_dump not found on PATH. Install the Postgres client tools "
            "(e.g. `apt-get install postgresql-client` or `brew install libpq` "
            "and add it to PATH) in whatever environment runs `flask backup-db`."
        )
    dest = os.path.join(dest_dir, f"rpsas-backup-{ts}.sql.gz")
    proc = subprocess.run(
        ["pg_dump", "--no-owner", "--no-privileges", "--clean", "--if-exists", database_url],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump failed (exit {proc.returncode}): {proc.stderr.decode(errors='replace')[:2000]}")
    with gzip.open(dest, "wb") as f_out:
        f_out.write(proc.stdout)
    return dest


def _upload_via_gh(path):
    """One private GitHub release per backup, tagged with the backup's own
    timestamp, with the gzip file attached as a release asset."""
    repo = current_app.config["BACKUP_GITHUB_REPO"]
    tag = os.path.basename(path)
    for suffix in (".gz", ".sql", ".db"):
        tag = tag[: -len(suffix)] if tag.endswith(suffix) else tag
    proc = subprocess.run(
        ["gh", "release", "create", tag, path, "--repo", repo, "--title", tag,
         "--notes", "Automated Sixth Vital database backup.", "--prerelease"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh release upload failed: {proc.stderr.decode(errors='replace')[:2000]}")
    return {"method": "github_release", "repo": repo, "tag": tag}


def _upload_via_s3(path):
    import boto3  # imported lazily -- only required if this path is actually used

    bucket = current_app.config["BACKUP_BUCKET"]
    key = f"backups/{os.path.basename(path)}"
    client_kwargs = {
        "aws_access_key_id": current_app.config["AWS_ACCESS_KEY_ID"],
        "aws_secret_access_key": current_app.config["AWS_SECRET_ACCESS_KEY"],
    }
    endpoint = current_app.config.get("BACKUP_S3_ENDPOINT_URL")
    if endpoint:
        client_kwargs["endpoint_url"] = endpoint
    client = boto3.client("s3", **client_kwargs)
    client.upload_file(path, bucket, key)
    return {"method": "s3", "bucket": bucket, "key": key}


def _pick_upload_method():
    """gh checked first -- no separate cloud account needed if the project
    already lives on GitHub. Falls back to S3-compatible if boto3 creds are
    set instead. Returns None if neither is configured."""
    if current_app.config.get("BACKUP_GITHUB_REPO") and shutil.which("gh"):
        return _upload_via_gh
    if (
        current_app.config.get("AWS_ACCESS_KEY_ID")
        and current_app.config.get("AWS_SECRET_ACCESS_KEY")
        and current_app.config.get("BACKUP_BUCKET")
    ):
        return _upload_via_s3
    return None


def run_backup():
    """Dumps the database and uploads it. Returns the upload result dict."""
    upload = _pick_upload_method()
    if upload is None:
        raise RuntimeError(
            "No backup destination configured. Set BACKUP_GITHUB_REPO (and "
            "install + `gh auth login` the `gh` CLI) for GitHub-release "
            "backups, OR AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / "
            "BACKUP_BUCKET for S3-compatible bucket backups."
        )
    with tempfile.TemporaryDirectory() as tmp:
        dump_path = dump_database(tmp)
        return upload(dump_path)
