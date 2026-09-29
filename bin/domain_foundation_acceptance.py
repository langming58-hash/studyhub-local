#!/usr/bin/env python3
"""Phase 1 domain foundation acceptance checks.

The checks use only temporary synthetic files. They verify the new Source /
Blob / Material / MaterialVersion projection without switching current product
reads away from the legacy compatibility tables.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_ENV = (
    "DATABASE_PATH",
    "DEMO_MODE",
    "OPENAI_API_KEY",
    "OPENAI_VECTOR_STORE_ID",
    "STUDYHUB_CACHE_DIR",
    "STUDYHUB_CONFIG_PATH",
    "STUDYHUB_DATA_DIR",
    "STUDYHUB_DESKTOP_TEST_ROOT",
    "STUDYHUB_LOG_DIR",
    "STUDYHUB_RUNTIME_DIR",
    "STUDYHUB_RUNTIME_PROFILE",
    "STUDY_LIBRARY_PATH",
)


def load_server(tmp: Path, library: Path, database: Path):
    for key in STATE_ENV:
        os.environ.pop(key, None)
    runtime = database.parent
    os.environ.update(
        {
            "STUDYHUB_RUNTIME_PROFILE": "production",
            "STUDY_LIBRARY_PATH": str(library),
            "STUDYHUB_RUNTIME_DIR": str(runtime),
            "STUDYHUB_DATA_DIR": str(runtime / "data"),
            "STUDYHUB_CACHE_DIR": str(runtime / "cache"),
            "STUDYHUB_LOG_DIR": str(runtime / "logs"),
            "STUDYHUB_CONFIG_PATH": str(runtime / "settings.env"),
            "DATABASE_PATH": str(database),
            "OPENAI_API_KEY": "",
            "OPENAI_VECTOR_STORE_ID": "",
        }
    )
    module_name = f"studyhub_domain_acceptance_{hashlib.sha256(str(tmp).encode()).hexdigest()[:10]}"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "server.py")
    server = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    server.DATA_DIR = runtime / "data"
    server.CACHE_DIR = runtime / "cache"
    server.TEXT_CACHE_DIR = server.CACHE_DIR / "text"
    server.LOG_DIR = runtime / "logs"
    server.DB_PATH = database
    server.ENV_LOCAL_PATH = runtime / "settings.env"
    server.ENV_LOCAL_EXISTS = False
    server.DEFAULT_STUDY_ROOT = library
    server.ensure_dirs()
    return server


def connect(server: Any) -> sqlite3.Connection:
    conn = server.connect_db()
    server.init_db(conn)
    return conn


def write_text(path: Path, text: str, *, mtime: int = 1_700_000_000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(scalar(conn, f"SELECT COUNT(*) FROM {table}"))


def file_row(conn: sqlite3.Connection, filename: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM files WHERE filename=? ORDER BY id LIMIT 1", (filename,)).fetchone()
    assert row is not None, filename
    return row


def domain_material(conn: sqlite3.Connection, legacy_file_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM materials WHERE legacy_file_id=?", (legacy_file_id,)).fetchone()
    assert row is not None, legacy_file_id
    return row


def domain_current_version(conn: sqlite3.Connection, material_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT mv.* FROM material_versions mv JOIN materials m ON m.current_version_id=mv.id WHERE m.id=?",
        (material_id,),
    ).fetchone()
    assert row is not None, material_id
    return row


def build_library(library: Path) -> tuple[Path, Path]:
    first = write_text(
        library / "TEST6101 - Domain Foundation" / "Week 01" / "01 Course Materials" / "Lecture" / "Domain Lecture.txt",
        "Domain foundation alpha-content. This source-owned original must remain unchanged.",
    )
    second = write_text(
        library / "TEST6101 - Domain Foundation" / "Week 01" / "02 Exercises" / "Tutorial" / "Domain Tutorial.txt",
        "Teacher-provided synthetic tutorial question. Q1: Explain the domain model.",
    )
    return first, second


def check_schema_and_migration(server: Any, conn: sqlite3.Connection) -> bool:
    required = {
        "schema_migrations",
        "sources",
        "blobs",
        "materials",
        "material_versions",
    }
    existing_tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    phase1_recorded = bool(
        conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=1 AND name='phase1_domain_foundation'"
        ).fetchone()
    )
    material_columns = table_columns(conn, "materials")
    version_columns = table_columns(conn, "material_versions")
    source_columns = table_columns(conn, "sources")
    blob_columns = table_columns(conn, "blobs")
    before = table_count(conn, "schema_migrations")
    server.run_schema_migrations(conn)
    after = table_count(conn, "schema_migrations")
    failed_not_recorded = False
    try:
        server.apply_schema_migration(
            conn,
            99_999,
            "synthetic_failing_migration",
            lambda failing_conn: failing_conn.execute("CREATE TABLE synthetic_should_rollback(id INTEGER)")
            and (_ for _ in ()).throw(RuntimeError("intentional synthetic failure")),
        )
    except RuntimeError:
        failed_not_recorded = not conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version=99999"
        ).fetchone() and "synthetic_should_rollback" not in {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    return (
        required <= existing_tables
        and phase1_recorded
        and before == after
        and failed_not_recorded
        and {"stable_id", "legacy_file_id", "current_version_id", "removed_at"} <= material_columns
        and {"material_id", "source_id", "blob_id", "legacy_file_version_id", "source_missing"} <= version_columns
        and {"provider_kind", "authority", "local_locator", "remote_id", "fetched_at"} <= source_columns
        and {"stable_id", "sha256", "byte_size", "mime_type"} <= blob_columns
    )


def check_projection_and_backfill(server: Any, conn: sqlite3.Connection, original: Path) -> bool:
    server.scan_library(server.DEFAULT_STUDY_ROOT)
    lecture = file_row(conn, "Domain Lecture.txt")
    material = domain_material(conn, lecture["id"])
    current_version = domain_current_version(conn, material["id"])
    source = conn.execute("SELECT * FROM sources WHERE id=?", (current_version["source_id"],)).fetchone()
    blob = conn.execute("SELECT * FROM blobs WHERE id=?", (current_version["blob_id"],)).fetchone()
    public_payload = server.public_file(lecture)
    initial_counts = {table: table_count(conn, table) for table in ("materials", "material_versions", "sources", "blobs")}
    server.scan_library(server.DEFAULT_STUDY_ROOT)
    unchanged_counts = {table: table_count(conn, table) for table in ("materials", "material_versions", "sources", "blobs")}
    return (
        material["stable_id"] == lecture["stable_id"]
        and material["legacy_file_id"] == lecture["id"]
        and material["current_version_id"] == current_version["id"]
        and current_version["sha256"] == lecture["sha256"]
        and current_version["legacy_file_id"] == lecture["id"]
        and source["provider_kind"] == "local"
        and source["authority"] == "USER_OWNED"
        and source["local_locator"] == str(original.resolve())
        and blob["sha256"] == lecture["sha256"]
        and blob["byte_size"] == lecture["file_size"]
        and "current_version_id" not in public_payload
        and "local_locator" not in public_payload
        and initial_counts == unchanged_counts
    )


def check_versioning_identity_and_relink(server: Any, conn: sqlite3.Connection, original: Path, tmp: Path) -> bool:
    before_hash = digest(original)
    lecture = file_row(conn, "Domain Lecture.txt")
    material_before = domain_material(conn, lecture["id"])
    stable_id = material_before["stable_id"]
    write_text(original, "Domain foundation beta-content. Updated local original.", mtime=1_700_000_200)
    server.scan_library(server.DEFAULT_STUDY_ROOT)
    updated = file_row(conn, "Domain Lecture.txt")
    material_after_update = domain_material(conn, updated["id"])
    search_matches = server.search_local_context(conn, "beta-content", {"fileId": updated["id"]}, limit=3)
    version_count = scalar(conn, "SELECT COUNT(*) FROM material_versions WHERE material_id=?", (material_after_update["id"],))
    blob_count = scalar(
        conn,
        "SELECT COUNT(DISTINCT blob_id) FROM material_versions WHERE material_id=?",
        (material_after_update["id"],),
    )
    external = write_text(tmp / "external" / "Relinked Lecture.txt", "Domain relinked gamma-content.", mtime=1_700_000_300)
    server.manage_materials(conn, {"action": "relink", "id": updated["id"], "path": str(external)})
    relinked = file_row(conn, "Relinked Lecture.txt")
    relinked_material = domain_material(conn, relinked["id"])
    relinked_version = domain_current_version(conn, relinked_material["id"])
    relinked_source = conn.execute("SELECT * FROM sources WHERE id=?", (relinked_version["source_id"],)).fetchone()
    tutorial = file_row(conn, "Domain Tutorial.txt")
    removed = server.manage_materials(conn, {"action": "remove", "id": tutorial["id"]})
    removed_material = domain_material(conn, tutorial["id"])
    return (
        before_hash != digest(original)
        and material_after_update["stable_id"] == stable_id
        and updated["sha256"] != lecture["sha256"]
        and version_count >= 2
        and blob_count >= 2
        and any("beta-content" in row["text"] for row in search_matches)
        and relinked_material["stable_id"] == stable_id
        and relinked_material["active"] == 1
        and relinked_source["local_locator"] == str(external.resolve())
        and "gamma-content" in server.read_cached_text(relinked, 500)
        and removed["ok"] is True
        and removed_material["active"] == 0
        and bool(removed_material["removed_at"])
    )


def check_reset_and_source_integrity(server: Any, conn: sqlite3.Connection, originals: list[Path]) -> bool:
    before = {str(path): digest(path) for path in originals if path.exists()}
    server.reset_studyhub_state(conn)
    after = {str(path): digest(path) for path in originals if path.exists()}
    return (
        before == after
        and table_count(conn, "files") == 0
        and table_count(conn, "materials") == 0
        and table_count(conn, "material_versions") == 0
        and table_count(conn, "sources") == 0
        and table_count(conn, "blobs") == 0
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="studyhub-domain-foundation-") as tmp_name:
        tmp = Path(tmp_name)
        library = tmp / "StudyLibrary"
        database = tmp / "runtime" / "studyhub.sqlite"
        original, tutorial = build_library(library)
        server = load_server(tmp, library, database)
        conn = connect(server)
        checks = {
            "schema_migration_versioned_and_atomic": check_schema_and_migration(server, conn),
            "domain_projection_backfills_current_files": check_projection_and_backfill(server, conn, original),
            "material_identity_versions_and_relink": check_versioning_identity_and_relink(server, conn, original, tmp),
            "reset_clears_domain_projection_and_preserves_sources": check_reset_and_source_integrity(server, conn, [original, tutorial]),
        }
        conn.close()

    for name, ok in checks.items():
        print(f"{name}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
