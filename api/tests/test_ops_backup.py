"""ops/backup.py -- dump + upload tests. No real pg_dump, gh, or boto3
network calls: subprocess and boto3 are mocked throughout. The SQLite path
is exercised for real (against a small temp file) since it's just a plain
file copy + gzip, no subprocess involved."""

import gzip
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))  # repo root, for `ops.*`

import pytest

from ops import backup


# ── dump_database: sqlite path (real file I/O, no subprocess) ────────────

def test_dump_database_sqlite_copies_and_gzips_file(app, tmp_path):
    src = tmp_path / "dev.db"
    src.write_bytes(b"pretend-sqlite-file-bytes")

    with app.app_context():
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{src}"
        dest_dir = tmp_path / "out"
        dest_dir.mkdir()
        result_path = backup.dump_database(str(dest_dir))

    assert result_path.endswith(".db.gz")
    assert os.path.exists(result_path)
    with gzip.open(result_path, "rb") as f:
        assert f.read() == b"pretend-sqlite-file-bytes"


def test_dump_database_sqlite_missing_file_raises(app, tmp_path):
    with app.app_context():
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{tmp_path}/does-not-exist.db"
        with pytest.raises(RuntimeError, match="not found"):
            backup.dump_database(str(tmp_path))


# ── dump_database: postgres path (subprocess mocked) ─────────────────────

def test_dump_database_postgres_missing_pg_dump_raises(app, tmp_path):
    with app.app_context():
        app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://user:pw@host/db"
        with patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="pg_dump"):
                backup.dump_database(str(tmp_path))


def test_dump_database_postgres_calls_pg_dump_and_gzips_output(app, tmp_path):
    fake_dump_bytes = b"-- fake pg_dump SQL output --"
    fake_proc = subprocess.CompletedProcess(args=["pg_dump"], returncode=0, stdout=fake_dump_bytes, stderr=b"")

    with app.app_context():
        app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://user:pw@host/db"
        with patch("shutil.which", return_value="/usr/bin/pg_dump"), \
             patch("ops.backup.subprocess.run", return_value=fake_proc) as mock_run:
            result_path = backup.dump_database(str(tmp_path))

    assert result_path.endswith(".sql.gz")
    with gzip.open(result_path, "rb") as f:
        assert f.read() == fake_dump_bytes
    called_cmd = mock_run.call_args[0][0]
    assert called_cmd[0] == "pg_dump"
    assert "postgresql://user:pw@host/db" in called_cmd


def test_dump_database_postgres_nonzero_exit_raises(app, tmp_path):
    fake_proc = subprocess.CompletedProcess(args=["pg_dump"], returncode=1, stdout=b"", stderr=b"connection refused")

    with app.app_context():
        app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://user:pw@host/db"
        with patch("shutil.which", return_value="/usr/bin/pg_dump"), \
             patch("ops.backup.subprocess.run", return_value=fake_proc):
            with pytest.raises(RuntimeError, match="pg_dump failed"):
                backup.dump_database(str(tmp_path))


# ── upload method selection ───────────────────────────────────────────────

def test_pick_upload_method_none_configured_returns_none(app):
    with app.app_context():
        app.config["BACKUP_GITHUB_REPO"] = ""
        app.config["AWS_ACCESS_KEY_ID"] = ""
        app.config["AWS_SECRET_ACCESS_KEY"] = ""
        app.config["BACKUP_BUCKET"] = ""
        assert backup._pick_upload_method() is None


def test_pick_upload_method_prefers_gh_when_both_configured(app):
    with app.app_context():
        app.config["BACKUP_GITHUB_REPO"] = "acme/rpsas"
        app.config["AWS_ACCESS_KEY_ID"] = "AKIA..."
        app.config["AWS_SECRET_ACCESS_KEY"] = "secret"
        app.config["BACKUP_BUCKET"] = "rpsas-backups"
        with patch("shutil.which", return_value="/usr/bin/gh"):
            assert backup._pick_upload_method() is backup._upload_via_gh


def test_pick_upload_method_gh_repo_set_but_cli_missing_falls_back_to_s3(app):
    with app.app_context():
        app.config["BACKUP_GITHUB_REPO"] = "acme/rpsas"
        app.config["AWS_ACCESS_KEY_ID"] = "AKIA..."
        app.config["AWS_SECRET_ACCESS_KEY"] = "secret"
        app.config["BACKUP_BUCKET"] = "rpsas-backups"
        with patch("shutil.which", return_value=None):  # `gh` not on PATH
            assert backup._pick_upload_method() is backup._upload_via_s3


def test_run_backup_raises_actionable_error_when_nothing_configured(app):
    with app.app_context():
        app.config["BACKUP_GITHUB_REPO"] = ""
        app.config["AWS_ACCESS_KEY_ID"] = ""
        app.config["AWS_SECRET_ACCESS_KEY"] = ""
        app.config["BACKUP_BUCKET"] = ""
        with pytest.raises(RuntimeError, match="No backup destination configured"):
            backup.run_backup()


# ── gh upload ──────────────────────────────────────────────────────────────

def test_upload_via_gh_calls_gh_release_create(app, tmp_path):
    fake_proc = subprocess.CompletedProcess(args=["gh"], returncode=0, stdout=b"", stderr=b"")
    dump_file = tmp_path / "rpsas-backup-20260928-060000.sql.gz"
    dump_file.write_bytes(b"x")

    with app.app_context():
        app.config["BACKUP_GITHUB_REPO"] = "acme/rpsas"
        with patch("ops.backup.subprocess.run", return_value=fake_proc) as mock_run:
            result = backup._upload_via_gh(str(dump_file))

    assert result == {"method": "github_release", "repo": "acme/rpsas", "tag": "rpsas-backup-20260928-060000"}
    called_cmd = mock_run.call_args[0][0]
    assert called_cmd[:3] == ["gh", "release", "create"]
    assert "acme/rpsas" in called_cmd


def test_upload_via_gh_nonzero_exit_raises(app, tmp_path):
    fake_proc = subprocess.CompletedProcess(args=["gh"], returncode=1, stdout=b"", stderr=b"not authenticated")
    dump_file = tmp_path / "rpsas-backup-x.sql.gz"
    dump_file.write_bytes(b"x")

    with app.app_context():
        app.config["BACKUP_GITHUB_REPO"] = "acme/rpsas"
        with patch("ops.backup.subprocess.run", return_value=fake_proc):
            with pytest.raises(RuntimeError, match="gh release upload failed"):
                backup._upload_via_gh(str(dump_file))


# ── S3 upload (boto3 mocked) ────────────────────────────────────────────

def test_upload_via_s3_calls_boto3_client_and_upload_file(app, tmp_path):
    dump_file = tmp_path / "rpsas-backup-20260928-060000.sql.gz"
    dump_file.write_bytes(b"x")

    mock_client = MagicMock()
    with app.app_context():
        app.config["AWS_ACCESS_KEY_ID"] = "AKIA..."
        app.config["AWS_SECRET_ACCESS_KEY"] = "secret"
        app.config["BACKUP_BUCKET"] = "rpsas-backups"
        app.config["BACKUP_S3_ENDPOINT_URL"] = ""
        with patch("boto3.client", return_value=mock_client) as mock_boto_client:
            result = backup._upload_via_s3(str(dump_file))

    assert result == {"method": "s3", "bucket": "rpsas-backups", "key": "backups/rpsas-backup-20260928-060000.sql.gz"}
    mock_boto_client.assert_called_once()
    assert mock_boto_client.call_args[0][0] == "s3"
    assert "endpoint_url" not in mock_boto_client.call_args[1]
    mock_client.upload_file.assert_called_once_with(str(dump_file), "rpsas-backups", "backups/rpsas-backup-20260928-060000.sql.gz")


def test_upload_via_s3_passes_custom_endpoint_for_s3_compatible_hosts(app, tmp_path):
    dump_file = tmp_path / "rpsas-backup-x.sql.gz"
    dump_file.write_bytes(b"x")

    mock_client = MagicMock()
    with app.app_context():
        app.config["AWS_ACCESS_KEY_ID"] = "key"
        app.config["AWS_SECRET_ACCESS_KEY"] = "secret"
        app.config["BACKUP_BUCKET"] = "rpsas-backups"
        app.config["BACKUP_S3_ENDPOINT_URL"] = "https://xyz.r2.cloudflarestorage.com"
        with patch("boto3.client", return_value=mock_client) as mock_boto_client:
            backup._upload_via_s3(str(dump_file))

    assert mock_boto_client.call_args[1]["endpoint_url"] == "https://xyz.r2.cloudflarestorage.com"


# ── run_backup end-to-end (sqlite dump + mocked s3 upload) ───────────────

def test_run_backup_end_to_end_sqlite_to_s3(app, tmp_path):
    src = tmp_path / "dev.db"
    src.write_bytes(b"pretend-sqlite-bytes")

    mock_client = MagicMock()
    with app.app_context():
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{src}"
        app.config["BACKUP_GITHUB_REPO"] = ""
        app.config["AWS_ACCESS_KEY_ID"] = "key"
        app.config["AWS_SECRET_ACCESS_KEY"] = "secret"
        app.config["BACKUP_BUCKET"] = "rpsas-backups"
        app.config["BACKUP_S3_ENDPOINT_URL"] = ""
        with patch("boto3.client", return_value=mock_client):
            result = backup.run_backup()

    assert result["method"] == "s3"
    assert result["bucket"] == "rpsas-backups"
    mock_client.upload_file.assert_called_once()
