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


def build_library(library: Path) -> tuple[Path, Path, Path]:
    first = write_text(
        library / "TEST6101 - Domain Foundation" / "Week 01" / "01 Course Materials" / "Lecture" / "Domain Lecture.txt",
        "Domain foundation alpha-content. This source-owned original must remain unchanged.",
    )
    second = write_text(
        library / "TEST6101 - Domain Foundation" / "Week 01" / "02 Exercises" / "Tutorial" / "Domain Tutorial.txt",
        "Teacher-provided synthetic tutorial question. Q1: Explain the domain model.",
    )
    missing = write_text(
        library / "TEST6101 - Domain Foundation" / "Week 02" / "01 Course Materials" / "Lecture" / "Missing Later.txt",
        "Synthetic original that will be removed after domain projection.",
    )
    return first, second, missing


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
        and blob["local_locator"] == ""
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
    old_version = conn.execute(
        """
        SELECT mv.*, s.local_locator
        FROM material_versions mv
        JOIN sources s ON s.id=mv.source_id
        WHERE mv.material_id=? AND mv.sha256=?
        """,
        (material_after_update["id"], lecture["sha256"]),
    ).fetchone()
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
    old_version_after_relink = conn.execute(
        """
        SELECT mv.*, s.local_locator
        FROM material_versions mv
        JOIN sources s ON s.id=mv.source_id
        WHERE mv.material_id=? AND mv.sha256=?
        """,
        (relinked_material["id"], lecture["sha256"]),
    ).fetchone()
    tutorial = file_row(conn, "Domain Tutorial.txt")
    removed = server.manage_materials(conn, {"action": "remove", "id": tutorial["id"]})
    removed_material = domain_material(conn, tutorial["id"])
    return (
        before_hash != digest(original)
        and material_after_update["stable_id"] == stable_id
        and updated["sha256"] != lecture["sha256"]
        and version_count >= 2
        and blob_count >= 2
        and old_version is not None
        and old_version_after_relink is not None
        and old_version_after_relink["source_id"] == old_version["source_id"]
        and old_version_after_relink["local_locator"] == old_version["local_locator"]
        and old_version_after_relink["local_locator"] != str(external.resolve())
        and any("beta-content" in row["text"] for row in search_matches)
        and relinked_material["stable_id"] == stable_id
        and relinked_material["active"] == 1
        and relinked_source["local_locator"] == str(external.resolve())
        and "gamma-content" in server.read_cached_text(relinked, 500)
        and removed["ok"] is True
        and removed_material["active"] == 0
        and bool(removed_material["removed_at"])
    )


def check_orphan_source_blob_not_gc(server: Any, conn: sqlite3.Connection) -> bool:
    conn.execute(
        """
        INSERT INTO sources(stable_id, provider_kind, authority, source_label, source_type, import_mode,
          local_locator, created_at, updated_at)
        VALUES ('source_unlinked_future_synthetic', 'synthetic', 'REMOTE_AUTHORITATIVE',
          'Future synthetic source', 'Future Connector', 'future', '', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO blobs(stable_id, sha256, byte_size, mime_type, extension, local_locator, created_at, updated_at)
        VALUES ('blob_unlinked_future_synthetic', ?, 17, 'text/plain', '.txt', '', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
        """,
        ("0" * 64,),
    )
    conn.commit()
    server.reconcile_domain_projection(conn)
    conn.commit()
    return bool(
        conn.execute("SELECT 1 FROM sources WHERE stable_id='source_unlinked_future_synthetic'").fetchone()
        and conn.execute("SELECT 1 FROM blobs WHERE stable_id='blob_unlinked_future_synthetic'").fetchone()
    )


def check_missing_original_domain_provenance(server: Any, conn: sqlite3.Connection, missing: Path) -> bool:
    missing_row = file_row(conn, missing.name)
    material = domain_material(conn, missing_row["id"])
    current_version = domain_current_version(conn, material["id"])
    source = conn.execute("SELECT * FROM sources WHERE id=?", (current_version["source_id"],)).fetchone()
    before_versions = scalar(conn, "SELECT COUNT(*) FROM material_versions WHERE material_id=?", (material["id"],))
    conn.commit()
    missing.unlink()
    server.scan_library(server.DEFAULT_STUDY_ROOT)
    after_file = conn.execute("SELECT * FROM files WHERE id=?", (missing_row["id"],)).fetchone()
    after_material = domain_material(conn, missing_row["id"])
    after_versions = conn.execute("SELECT * FROM material_versions WHERE material_id=?", (after_material["id"],)).fetchall()
    after_source = conn.execute("SELECT * FROM sources WHERE id=?", (source["id"],)).fetchone()
    return (
        after_file["source_missing"] == 1
        and after_file["active"] == 1
        and after_material["stable_id"] == material["stable_id"]
        and after_material["active"] == 1
        and bool(after_versions)
        and len(after_versions) == before_versions
        and after_source is not None
        and all(version["source_missing"] == 1 for version in after_versions if version["active"])
    )


def check_batch_folder_import_reconciles_once(server: Any, conn: sqlite3.Connection, tmp: Path) -> bool:
    folder = tmp / "batch-import" / "TEST6201 - Batch Domain"
    for index in range(5):
        write_text(
            folder / "Week 01" / "01 Course Materials" / "Lecture" / f"Batch {index}.txt",
            f"Synthetic batch import material {index}.",
        )
    original_reconcile = server.reconcile_domain_projection
    calls = {"count": 0}

    def counted_reconcile(inner_conn: sqlite3.Connection) -> None:
        calls["count"] += 1
        original_reconcile(inner_conn)

    server.reconcile_domain_projection = counted_reconcile
    try:
        imported = server.import_course_folder(conn, {"path": str(folder), "is_official": True})
        batch_calls = calls["count"]
        course_id = int(imported["course"]["id"])
        week_id = conn.execute("SELECT id FROM weeks WHERE course_id=? LIMIT 1", (course_id,)).fetchone()["id"]
        standalone = write_text(tmp / "standalone" / "Standalone.txt", "Standalone synthetic file.")
        server.register_material_paths(
            conn,
            {"paths": [str(standalone)], "course_id": course_id, "week_id": week_id, "material_type": "lecture"},
        )
        standalone_added_call = calls["count"] == batch_calls + 1
    finally:
        server.reconcile_domain_projection = original_reconcile
    return imported["added"] == 5 and batch_calls == 1 and standalone_added_call


def check_duplicate_bytes_separate_materials(server: Any, conn: sqlite3.Connection, tmp: Path) -> bool:
    course = server.manage_course(conn, {"action": "create", "course_code": "TEST6301", "display_name": "Duplicate Bytes"})
    week = server.manage_week(conn, {"action": "create", "course_id": course["id"], "label": "Week 01", "kind": "week"})
    content = "Identical bytes with separate academic identities.\n"
    first = write_text(tmp / "dupes" / "Duplicate Identity A.txt", content)
    second = write_text(tmp / "dupes" / "Duplicate Identity B.txt", content)
    result = server.register_material_paths(
        conn,
        {
            "paths": [str(first), str(second)],
            "course_id": course["id"],
            "week_id": week["id"],
            "material_type": "reading",
            "duplicate_policy": "add_anyway",
            "is_official": True,
        },
    )
    ids = [item["id"] for item in result["items"] if item["status"] == "added"]
    if len(ids) != 2:
        return False
    materials = conn.execute(
        f"SELECT * FROM materials WHERE legacy_file_id IN ({','.join('?' for _ in ids)}) ORDER BY legacy_file_id",
        ids,
    ).fetchall()
    blob_ids = conn.execute(
        f"""
        SELECT DISTINCT mv.blob_id
        FROM material_versions mv
        JOIN materials m ON m.id=mv.material_id
        WHERE m.legacy_file_id IN ({','.join('?' for _ in ids)})
        """,
        ids,
    ).fetchall()
    return len(materials) == 2 and materials[0]["stable_id"] != materials[1]["stable_id"] and len(blob_ids) == 1


def check_user_associations_survive_reconciliation(server: Any, conn: sqlite3.Connection) -> bool:
    row = file_row(conn, "Relinked Lecture.txt")
    conn.execute(
        "INSERT INTO notes(target_type, target_id, course_id, week_label, body, created_at, updated_at) VALUES ('file', ?, ?, ?, 'Domain note', ?, ?)",
        (row["id"], row["course_id"], row["week_label"], server.now_iso(), server.now_iso()),
    )
    conn.execute("INSERT OR IGNORE INTO stars(target_type, target_id, created_at) VALUES ('file', ?, ?)", (row["id"], server.now_iso()))
    conn.commit()
    before = conn.execute("SELECT id, stable_id FROM files WHERE id=?", (row["id"],)).fetchone()
    server.reconcile_domain_projection(conn)
    after = conn.execute("SELECT id, stable_id FROM files WHERE id=?", (row["id"],)).fetchone()
    associations = conn.execute(
        "SELECT (SELECT COUNT(*) FROM notes WHERE target_id=?) + (SELECT COUNT(*) FROM stars WHERE target_id=?) AS c",
        (row["id"], row["id"]),
    ).fetchone()["c"]
    return before["id"] == after["id"] and before["stable_id"] == after["stable_id"] and associations == 2


def create_legacy_database(path: Path, original: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    sha = digest(original)
    conn = sqlite3.connect(path)
    now = "2026-01-01T00:00:00+00:00"
    conn.executescript(
        """
        CREATE TABLE courses (
          id INTEGER PRIMARY KEY, code TEXT NOT NULL, name TEXT NOT NULL,
          folder_name TEXT NOT NULL UNIQUE, path TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE weeks (
          id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL, week_label TEXT NOT NULL,
          week_number INTEGER, path TEXT NOT NULL, has_materials INTEGER NOT NULL DEFAULT 0,
          file_count INTEGER NOT NULL DEFAULT 0, UNIQUE(course_id, week_label)
        );
        CREATE TABLE files (
          id INTEGER PRIMARY KEY, course_id INTEGER NOT NULL, week_id INTEGER,
          course_code TEXT NOT NULL, week_label TEXT, section TEXT, category TEXT,
          exercise_type TEXT, filename TEXT NOT NULL, original_path TEXT NOT NULL UNIQUE,
          rel_path TEXT NOT NULL, source TEXT NOT NULL, source_label TEXT NOT NULL,
          hash TEXT NOT NULL, size INTEGER NOT NULL, modified_at TEXT NOT NULL,
          indexed_at TEXT NOT NULL, extension TEXT, mime_type TEXT,
          is_official INTEGER NOT NULL DEFAULT 1, suspicious TEXT DEFAULT '',
          text_cache_path TEXT DEFAULT ''
        );
        CREATE TABLE file_versions (
          id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
          stable_id TEXT NOT NULL, sha256 TEXT NOT NULL, file_size INTEGER NOT NULL,
          modified_at TEXT NOT NULL, indexed_at TEXT NOT NULL, text_cache_path TEXT DEFAULT '',
          active INTEGER NOT NULL DEFAULT 1, UNIQUE(file_id, sha256)
        );
        """
    )
    conn.execute(
        "INSERT INTO courses(id, code, name, folder_name, path, created_at, updated_at) VALUES (1, 'TEST6401', 'Legacy Domain', 'TEST6401 - Legacy Domain', ?, ?, ?)",
        (str(original.parent.parent.parent.parent), now, now),
    )
    conn.execute(
        "INSERT INTO weeks(id, course_id, week_label, week_number, path, has_materials, file_count) VALUES (1, 1, 'Week 01', 1, ?, 1, 1)",
        (str(original.parent.parent.parent),),
    )
    conn.execute(
        """
        INSERT INTO files(id, course_id, week_id, course_code, week_label, section, category, exercise_type,
          filename, original_path, rel_path, source, source_label, hash, size, modified_at, indexed_at,
          extension, mime_type, is_official, suspicious, text_cache_path)
        VALUES (1, 1, 1, 'TEST6401', 'Week 01', '01 Course Materials', 'Lecture', '',
          ?, ?, ?, 'official', 'Legacy synthetic source', ?, ?, ?, ?, '.txt', 'text/plain', 1, '', '')
        """,
        (original.name, str(original.resolve()), original.name, sha, original.stat().st_size, now, now),
    )
    conn.execute(
        "INSERT INTO file_versions(id, file_id, stable_id, sha256, file_size, modified_at, indexed_at, text_cache_path, active) VALUES (1, 1, 'legacy-version-stable', ?, ?, ?, ?, '', 1)",
        (sha, original.stat().st_size, now, now),
    )
    conn.commit()
    conn.close()
    return sha


def check_populated_legacy_database_migrates(tmp: Path) -> bool:
    library = tmp / "legacy-library"
    original = write_text(library / "TEST6401 - Legacy Domain" / "Week 01" / "Lecture" / "Legacy Material.txt", "Legacy synthetic original remains unchanged.")
    before_hash = digest(original)
    database = tmp / "legacy-runtime" / "studyhub.sqlite"
    legacy_sha = create_legacy_database(database, original)
    server = load_server(tmp / "legacy-loader", library, database)
    conn = connect(server)
    first_counts = {table: table_count(conn, table) for table in ("files", "file_versions", "materials", "material_versions", "sources", "blobs")}
    legacy_file = conn.execute("SELECT * FROM files WHERE id=1").fetchone()
    material = domain_material(conn, 1)
    version = domain_current_version(conn, material["id"])
    source = conn.execute("SELECT * FROM sources WHERE id=?", (version["source_id"],)).fetchone()
    blob = conn.execute("SELECT * FROM blobs WHERE id=?", (version["blob_id"],)).fetchone()
    server.init_db(conn)
    second_counts = {table: table_count(conn, table) for table in ("files", "file_versions", "materials", "material_versions", "sources", "blobs")}
    conn.close()
    return (
        legacy_file["id"] == 1
        and legacy_file["sha256"] == legacy_sha
        and material["legacy_file_id"] == 1
        and version["legacy_file_version_id"] == 1
        and source["local_locator"] == str(original.resolve())
        and blob["sha256"] == legacy_sha
        and blob["local_locator"] == ""
        and first_counts == second_counts
        and digest(original) == before_hash
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
        original, tutorial, missing = build_library(library)
        server = load_server(tmp, library, database)
        conn = connect(server)
        checks = {
            "schema_migration_versioned_and_atomic": check_schema_and_migration(server, conn),
            "domain_projection_backfills_current_files": check_projection_and_backfill(server, conn, original),
            "material_identity_versions_and_relink": check_versioning_identity_and_relink(server, conn, original, tmp),
            "unlinked_future_source_blob_not_gc": check_orphan_source_blob_not_gc(server, conn),
            "missing_original_domain_provenance": check_missing_original_domain_provenance(server, conn, missing),
            "folder_import_reconciles_once": check_batch_folder_import_reconciles_once(server, conn, tmp),
            "duplicate_bytes_keep_distinct_materials": check_duplicate_bytes_separate_materials(server, conn, tmp),
            "user_associations_survive_reconciliation": check_user_associations_survive_reconciliation(server, conn),
            "populated_legacy_database_migrates": check_populated_legacy_database_migrates(tmp),
            "reset_clears_domain_projection_and_preserves_sources": check_reset_and_source_integrity(server, conn, [original, tutorial, missing]),
        }
        conn.close()

    for name, ok in checks.items():
        print(f"{name}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
