# SPDX-License-Identifier: Apache-2.0
"""Tests for the P3.7 ``pce diagnose`` bundle (``pce_core.diagnose``).

Guarantees under test (TASK-005 §5.7):

- ``collect_diagnostics`` always writes a well-formed zip containing the
  expected member files, even on a fresh DB or with no logs on disk.
- Secrets in env and config are redacted before the bundle is written.
- ``include_logs=False`` skips the ``logs/`` folder entirely.
- CLI ``python -m pce_core.diagnose -o …`` produces the same bundle.
- ``GET /api/v1/diagnose`` streams an ``application/zip`` with the
  expected ``Content-Disposition`` filename and the redacted summary.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from pce_core import diagnose


@pytest.fixture
def isolated_data_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point every PCE component at a fresh data dir for the test."""
    monkeypatch.setenv("PCE_DATA_DIR", str(tmp_path))
    # The diagnose module reads DATA_DIR/DB_PATH through pce_core.config at
    # import time; mutate the module attributes so the fixture takes effect
    # even though config module was already imported.
    import pce_core.config as _cfg
    monkeypatch.setattr(_cfg, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(_cfg, "DB_PATH", tmp_path / "pce.db", raising=False)
    return tmp_path


def _read_zip(path_or_bytes) -> dict[str, bytes]:
    if isinstance(path_or_bytes, (bytes, bytearray)):
        zf = zipfile.ZipFile(io.BytesIO(bytes(path_or_bytes)))
    else:
        zf = zipfile.ZipFile(str(path_or_bytes))
    with zf:
        return {name: zf.read(name) for name in zf.namelist()}


# ---------------------------------------------------------------------------
# Redaction primitives
# ---------------------------------------------------------------------------

class TestRedaction:

    def test_redact_env_drops_non_allowlisted_names(self):
        red = diagnose._redact_env({
            "RANDOM_COMPANY_CONFIG": "hello",
            "PATH": "/usr/bin",
            "PCE_DATA_DIR": "/tmp/pce",
        })
        assert "RANDOM_COMPANY_CONFIG" not in red
        assert red["PCE_DATA_DIR"] == "/tmp/pce"

    def test_redact_env_replaces_secret_names(self):
        red = diagnose._redact_env({
            "PCE_API_KEY": "sk-XXXX",
            "PCE_AUTH_TOKEN": "ghp_YYYY",
            "PCE_OPENAI_SECRET": "super",
            "PCE_DATA_DIR": "/tmp/pce",
            "PCE_PROXY_HOST": "127.0.0.1",
        })
        assert red["PCE_API_KEY"] == "REDACTED"
        assert red["PCE_AUTH_TOKEN"] == "REDACTED"
        assert red["PCE_OPENAI_SECRET"] == "REDACTED"
        # Safe values come through verbatim
        assert red["PCE_PROXY_HOST"] == "127.0.0.1"

    def test_redact_dict_masks_secret_keys_recursively(self):
        red = diagnose._redact_dict({
            "host": "127.0.0.1",
            "api_key": "sk-real",
            "nested": {
                "ok": "keep me",
                "authorization": "Bearer xxx",
                "list": [{"token": "t"}, {"normal": "n"}],
            },
        })
        assert red["host"] == "127.0.0.1"
        assert red["api_key"] == "REDACTED"
        assert red["nested"]["ok"] == "keep me"
        assert red["nested"]["authorization"] == "REDACTED"
        assert red["nested"]["list"][0]["token"] == "REDACTED"
        assert red["nested"]["list"][1]["normal"] == "n"


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

class TestCollectors:

    def test_collect_summary_has_required_fields(self):
        s = diagnose.collect_summary()
        for key in ("pce_version", "generated_at", "generated_at_iso",
                    "python", "platform", "argv", "pid", "cwd"):
            assert key in s, f"missing {key!r}"
        # ISO timestamp must end in Z (UTC)
        assert s["generated_at_iso"].endswith("Z")

    def test_collect_config_never_includes_secret_values(self, monkeypatch):
        # Inject a fake config attribute with a secret-looking key.
        import pce_core.config as _cfg
        monkeypatch.setattr(_cfg, "OTEL_EXPORTER_OTLP_HEADERS",
                            "authorization=Bearer-secret", raising=False)
        cfg = diagnose.collect_config()
        blob = json.dumps(cfg)
        # Even if we added a secret-looking value, the redactor should have
        # caught it (either not included or REDACTED). Never the raw token.
        assert "Bearer-secret" not in blob

    def test_collect_config_reports_data_dir(self, isolated_data_dir):
        cfg = diagnose.collect_config()
        assert "DATA_DIR" in cfg
        assert str(isolated_data_dir) in str(cfg["DATA_DIR"])

    def test_collect_health_handles_missing_db(self, tmp_path):
        missing = tmp_path / "does_not_exist.db"
        health = diagnose.collect_health(db_path=missing)
        # Either returns a structured dict or an {error: …} dict; must not raise.
        assert isinstance(health, dict)

    def test_collect_migrations_reports_version(self, isolated_data_dir):
        # Initialise a fresh DB so migrations run.
        from pce_core import db as pce_db
        pce_db.init_db(isolated_data_dir / "pce.db")
        m = diagnose.collect_migrations(db_path=isolated_data_dir / "pce.db")
        assert "expected_version" in m
        assert "current_version" in m
        assert m["up_to_date"] is True
        assert len(m["applied"]) >= 1  # baseline always applied

    def test_collect_schema_returns_ddl(self, isolated_data_dir):
        from pce_core import db as pce_db
        pce_db.init_db(isolated_data_dir / "pce.db")
        schema = diagnose.collect_schema(db_path=isolated_data_dir / "pce.db")
        assert "raw_captures" in schema
        assert "sessions" in schema
        assert "CREATE TABLE" in schema.upper() or "CREATE " in schema

    def test_collect_packages_includes_self(self):
        pkgs = diagnose.collect_packages()
        # FastAPI is a required dep; it should always be listed.
        assert "fastapi" in pkgs.lower()

    def test_collect_pipeline_errors_empty_on_fresh_db(self, isolated_data_dir):
        from pce_core import db as pce_db
        pce_db.init_db(isolated_data_dir / "pce.db")
        rows = diagnose.collect_pipeline_errors(db_path=isolated_data_dir / "pce.db")
        assert rows == []

    def test_collect_pipeline_errors_returns_recent_rows(self, isolated_data_dir):
        from pce_core import db as pce_db
        pce_db.init_db(isolated_data_dir / "pce.db")
        for i in range(5):
            pce_db.record_pipeline_error(
                "normalize", f"oops {i}",
                db_path=isolated_data_dir / "pce.db",
            )
        rows = diagnose.collect_pipeline_errors(db_path=isolated_data_dir / "pce.db", limit=3)
        assert len(rows) == 3

    def test_collect_logs_gracefully_empty(self, tmp_path):
        entries = diagnose.collect_logs(log_dirs=[tmp_path])
        assert entries == []

    def test_collect_logs_tails_large_files(self, tmp_path):
        # Write a log much larger than the tail cap; verify we only keep the end.
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        big = log_dir / "big.log"
        payload = b"x" * (diagnose._LOG_TAIL_BYTES + 5_000)
        with big.open("wb") as fh:
            fh.write(payload)
        entries = diagnose.collect_logs(log_dirs=[log_dir])
        assert len(entries) == 1
        arc, data = entries[0]
        assert arc == "logs/big.log"
        assert len(data) <= diagnose._LOG_TAIL_BYTES


# ---------------------------------------------------------------------------
# Bundle builder
# ---------------------------------------------------------------------------

_REQUIRED_MEMBERS = {
    "README.md",
    "summary.json",
    "config.json",
    "env.json",
    "health.json",
    "stats.json",
    "migrations.json",
    "schema.sql",
    "packages.txt",
    "pipeline_errors.jsonl",
}


class TestBundle:

    def test_collect_diagnostics_writes_zip_with_all_members(self, isolated_data_dir):
        # Fresh DB so migration/schema collectors succeed.
        from pce_core import db as pce_db
        pce_db.init_db(isolated_data_dir / "pce.db")

        out = diagnose.collect_diagnostics(
            output_path=isolated_data_dir / "bundle.zip",
            include_logs=False,
        )
        assert out.exists()
        members = set(_read_zip(out).keys())
        assert _REQUIRED_MEMBERS.issubset(members)
        # include_logs=False means zero logs/ entries.
        assert not any(m.startswith("logs/") for m in members)

    def test_bundle_summary_json_is_valid(self, isolated_data_dir):
        from pce_core import db as pce_db
        pce_db.init_db(isolated_data_dir / "pce.db")

        out = diagnose.collect_diagnostics(
            output_path=isolated_data_dir / "bundle.zip",
            include_logs=False,
        )
        payload = _read_zip(out)["summary.json"]
        data = json.loads(payload.decode("utf-8"))
        assert data["pce_version"]
        assert data["platform"]["system"]
        assert data["python"]["version"]

    def test_bundle_env_is_redacted(self, isolated_data_dir, monkeypatch):
        monkeypatch.setenv("PCE_API_KEY", "sk-never-share")
        monkeypatch.setenv("PCE_DATA_DIR", str(isolated_data_dir))
        from pce_core import db as pce_db
        pce_db.init_db(isolated_data_dir / "pce.db")

        out = diagnose.collect_diagnostics(
            output_path=isolated_data_dir / "bundle.zip",
            include_logs=False,
        )
        env = json.loads(_read_zip(out)["env.json"].decode("utf-8"))
        assert env.get("PCE_API_KEY") == "REDACTED"
        assert "sk-never-share" not in json.dumps(env)

    def test_bundle_includes_logs_when_requested(self, isolated_data_dir, monkeypatch):
        logs_dir = isolated_data_dir / "logs"
        logs_dir.mkdir()
        (logs_dir / "server.log").write_text("hello from the log\n", encoding="utf-8")
        monkeypatch.setenv("PCE_LOG_DIR", str(logs_dir))

        from pce_core import db as pce_db
        pce_db.init_db(isolated_data_dir / "pce.db")

        out = diagnose.collect_diagnostics(
            output_path=isolated_data_dir / "bundle.zip",
            include_logs=True,
        )
        members = _read_zip(out)
        assert "logs/server.log" in members
        assert b"hello from the log" in members["logs/server.log"]

    def test_bundle_bytes_variant_matches_file_variant(self, isolated_data_dir):
        from pce_core import db as pce_db
        pce_db.init_db(isolated_data_dir / "pce.db")

        data = diagnose.collect_diagnostics_bytes(include_logs=False)
        # Valid zip?
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = set(zf.namelist())
        assert _REQUIRED_MEMBERS.issubset(members)

    def test_bundle_default_output_location(self, isolated_data_dir):
        from pce_core import db as pce_db
        pce_db.init_db(isolated_data_dir / "pce.db")

        out = diagnose.collect_diagnostics(include_logs=False)
        assert out.parent == isolated_data_dir / "diagnose"
        assert out.suffix == ".zip"
        assert out.exists()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCli:

    def test_cli_writes_output_file(self, isolated_data_dir, capsys):
        from pce_core import db as pce_db
        pce_db.init_db(isolated_data_dir / "pce.db")

        target = isolated_data_dir / "cli-bundle.zip"
        rc = diagnose._cli(["-o", str(target), "--no-logs"])
        assert rc == 0
        assert target.exists()
        captured = capsys.readouterr()
        assert "Wrote diagnose bundle" in captured.out

    def test_cli_stdout_emits_zip_bytes(self, isolated_data_dir, capsysbinary):
        from pce_core import db as pce_db
        pce_db.init_db(isolated_data_dir / "pce.db")

        rc = diagnose._cli(["--stdout", "--no-logs"])
        assert rc == 0
        data = capsysbinary.readouterr().out
        # First 4 bytes of any zip are the local-file-header signature.
        assert data.startswith(b"PK\x03\x04")


# ---------------------------------------------------------------------------
# HTTP endpoint
# ---------------------------------------------------------------------------

class TestHttpEndpoint:

    def test_diagnose_endpoint_returns_zip(self, isolated_data_dir):
        # Import lazily so the fixture's PCE_DATA_DIR is honoured.
        from fastapi.testclient import TestClient
        from pce_core.server import app

        with TestClient(app) as client:
            resp = client.get("/api/v1/diagnose?include_logs=false")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        disp = resp.headers.get("content-disposition", "")
        assert "pce-diag-" in disp and disp.endswith(".zip")
        # Body must be a valid zip with the required members.
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            members = set(zf.namelist())
        assert _REQUIRED_MEMBERS.issubset(members)
