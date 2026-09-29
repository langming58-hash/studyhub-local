#!/usr/bin/env python3
"""Phase 2 local ingestion-core acceptance checks.

All inputs are synthetic temporary files. The checks prove current local
scanner/import behavior flows through one StudyHub-owned ingestion boundary
without introducing remote sync, Canvas, new schema, or public API changes.
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
    module_name = f"studyhub_ingestion_core_{hashlib.sha256(str(tmp).encode()).hexdigest()[:10]}"
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


def write_bytes(path: Path, data: bytes, *, mtime: int = 1_700_000_000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


def file_by_name(conn: sqlite3.Connection, filename: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM files WHERE filename=? ORDER BY id LIMIT 1", (filename,)).fetchone()
    assert row is not None, filename
    return row


def material_for_file(conn: sqlite3.Connection, file_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM materials WHERE legacy_file_id=?", (file_id,)).fetchone()
    assert row is not None, file_id
    return row


def active_version(conn: sqlite3.Connection, material_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM material_versions WHERE material_id=? AND active=1 ORDER BY id DESC LIMIT 1",
        (material_id,),
    ).fetchone()
    assert row is not None, material_id
    return row


def make_candidate(server: Any, path: Path, course: sqlite3.Row | dict[str, Any], week: sqlite3.Row | dict[str, Any], *, material_type: str = "reading", identity: Any = None):
    category = server.material_type_label(material_type)
    section = server.section_for_material_type(material_type)
    return server.IngestionCandidate(
        path=path,
        course_id=int(course["id"]),
        week_id=int(week["id"]),
        course_code=course["course_code"],
        course_name=course["display_name"],
        week_label=week["week_label"],
        week_number=week["week_number"],
        section=section,
        category=category,
        exercise_type=category if section == "02 Exercises" else "",
        material_type=material_type,
        source="official",
        source_label="Teacher-provided material",
        source_type="Local Reference",
        rel_path=f"references/test/{path.name}",
        import_mode="reference",
        is_official=1,
        is_solution=0,
        is_question_source=0,
        metadata_locked=1,
        display_name=path.name,
        stable_id=server.new_stable_id("material"),
        force_index=True,
        identity=identity,
    )


def install_ingestion_counter(server: Any) -> dict[str, Any]:
    original = server.ingest_material_candidate
    calls: list[dict[str, Any]] = []

    def counted(conn: sqlite3.Connection, candidate: Any):
        result = original(conn, candidate)
        calls.append(
            {
                "path": str(candidate.path),
                "import_mode": candidate.import_mode,
                "status": result.status,
                "file_id": result.legacy_file_id,
            }
        )
        return result

    server.ingest_material_candidate = counted
    return {"calls": calls, "restore": lambda: setattr(server, "ingest_material_candidate", original)}


def check_direct_helper_transaction_and_targeted_domain(tmp: Path) -> bool:
    library = tmp / "direct-library"
    database = tmp / "direct-runtime" / "studyhub.sqlite"
    server = load_server(tmp / "direct-loader", library, database)
    conn = connect(server)
    course = server.manage_course(conn, {"action": "create", "course_code": "TEST7001", "display_name": "Direct Ingestion"})
    week = server.manage_week(conn, {"action": "create", "course_id": course["id"], "label": "Week 01", "kind": "week"})
    assert conn.in_transaction is False
    original = write_text(tmp / "direct" / "Direct Material.txt", "Direct helper delta-content is indexed.")
    result = server.ingest_material_candidate(conn, make_candidate(server, original, course, week))
    row = conn.execute("SELECT * FROM files WHERE id=?", (result.legacy_file_id,)).fetchone()
    material = material_for_file(conn, result.legacy_file_id)
    version = active_version(conn, material["id"])
    source = conn.execute("SELECT * FROM sources WHERE id=?", (version["source_id"],)).fetchone()
    blob = conn.execute("SELECT * FROM blobs WHERE id=?", (version["blob_id"],)).fetchone()
    in_transaction_after_helper = conn.in_transaction
    conn.rollback()
    row_after_rollback = conn.execute("SELECT * FROM files WHERE id=?", (result.legacy_file_id,)).fetchone()
    material_after_rollback = conn.execute("SELECT * FROM materials WHERE legacy_file_id=?", (result.legacy_file_id,)).fetchone()
    conn.close()
    reopened = sqlite3.connect(database)
    reopened.row_factory = sqlite3.Row
    persisted = reopened.execute("SELECT * FROM files WHERE filename='Direct Material.txt'").fetchone()
    reopened.close()
    return (
        in_transaction_after_helper is True
        and row is not None
        and material is not None
        and version is not None
        and source["local_locator"] == str(original.resolve())
        and blob["sha256"] == digest(original)
        and row_after_rollback is None
        and material_after_rollback is None
        and persisted is None
    )


def check_scanned_and_modified_material(tmp: Path) -> bool:
    library = tmp / "scan-library"
    original = write_text(
        library / "TEST7101 - Ingestion Core" / "Week 01" / "01 Course Materials" / "Lecture" / "Core Lecture.txt",
        "Ingestion core alpha-content is searchable.",
    )
    original_hash = digest(original)
    server = load_server(tmp / "scan-loader", library, tmp / "scan-runtime" / "studyhub.sqlite")
    counter = install_ingestion_counter(server)
    first = server.scan_library(library)
    conn = connect(server)
    row_v1 = file_by_name(conn, "Core Lecture.txt")
    material_v1 = material_for_file(conn, row_v1["id"])
    version_v1 = active_version(conn, material_v1["id"])
    source_v1 = conn.execute("SELECT * FROM sources WHERE id=?", (version_v1["source_id"],)).fetchone()
    blob_v1 = conn.execute("SELECT * FROM blobs WHERE id=?", (version_v1["blob_id"],)).fetchone()
    search_v1 = server.search_local_context(conn, "alpha-content", {"fileId": row_v1["id"]}, limit=3)
    write_text(original, "Ingestion core beta-content is searchable after modification.", mtime=1_700_000_200)
    server.scan_library(library)
    row_v2 = file_by_name(conn, "Core Lecture.txt")
    material_v2 = material_for_file(conn, row_v2["id"])
    versions = conn.execute("SELECT * FROM material_versions WHERE material_id=? ORDER BY id", (material_v2["id"],)).fetchall()
    blob_count = scalar(
        conn,
        "SELECT COUNT(DISTINCT blob_id) FROM material_versions WHERE material_id=?",
        (material_v2["id"],),
    )
    search_v2 = server.search_local_context(conn, "beta-content", {"fileId": row_v2["id"]}, limit=3)
    conn.close()
    counter["restore"]()
    return (
        first.new_files == 1
        and any(call["import_mode"] == "scanned" for call in counter["calls"])
        and row_v1["id"] == row_v2["id"]
        and material_v1["stable_id"] == material_v2["stable_id"]
        and version_v1["sha256"] == original_hash
        and source_v1["local_locator"] == str(original.resolve())
        and blob_v1["sha256"] == original_hash
        and any("alpha-content" in match["text"] for match in search_v1)
        and row_v2["sha256"] != original_hash
        and len(versions) == 2
        and blob_count == 2
        and any(version["active"] == 0 and version["sha256"] == original_hash for version in versions)
        and any("beta-content" in match["text"] for match in search_v2)
    )


def check_manual_reference_and_duplicates(tmp: Path) -> bool:
    library = tmp / "manual-library"
    server = load_server(tmp / "manual-loader", library, tmp / "manual-runtime" / "studyhub.sqlite")
    conn = connect(server)
    counter = install_ingestion_counter(server)
    course = server.manage_course(conn, {"action": "create", "course_code": "TEST7201", "display_name": "Manual Ingestion"})
    week = server.manage_week(conn, {"action": "create", "course_id": course["id"], "label": "Week 02", "kind": "week"})
    reference = write_text(tmp / "external" / "Reference Reading.txt", "Manual reference gamma-content remains searchable.")
    before = digest(reference)
    first = server.register_material_paths(
        conn,
        {
            "paths": [str(reference)],
            "course_id": course["id"],
            "week_id": week["id"],
            "material_type": "reading",
            "is_official": True,
        },
    )
    row = file_by_name(conn, "Reference Reading.txt")
    material = material_for_file(conn, row["id"])
    version = active_version(conn, material["id"])
    source = conn.execute("SELECT * FROM sources WHERE id=?", (version["source_id"],)).fetchone()
    search = server.search_local_context(conn, "gamma-content", {"fileId": row["id"]}, limit=3)
    duplicate = write_text(tmp / "external" / "Reference Duplicate.txt", reference.read_text(encoding="utf-8"))
    skipped = server.register_material_paths(
        conn,
        {"paths": [str(duplicate)], "course_id": course["id"], "week_id": week["id"], "material_type": "reading"},
    )
    added_anyway = server.register_material_paths(
        conn,
        {
            "paths": [str(duplicate)],
            "course_id": course["id"],
            "week_id": week["id"],
            "material_type": "reading",
            "duplicate_policy": "add_anyway",
        },
    )
    duplicate_rows = conn.execute("SELECT * FROM files WHERE sha256=? AND active=1 ORDER BY id", (row["sha256"],)).fetchall()
    duplicate_materials = conn.execute(
        "SELECT stable_id FROM materials WHERE legacy_file_id IN (?, ?) ORDER BY legacy_file_id",
        (duplicate_rows[0]["id"], duplicate_rows[-1]["id"]),
    ).fetchall()
    blob_count = scalar(
        conn,
        """
        SELECT COUNT(DISTINCT mv.blob_id)
        FROM material_versions mv JOIN materials m ON m.id=mv.material_id
        WHERE m.legacy_file_id IN (?, ?)
        """,
        (duplicate_rows[0]["id"], duplicate_rows[-1]["id"]),
    )
    conn.close()
    counter["restore"]()
    return (
        first["added"] == 1
        and any(call["import_mode"] == "reference" for call in counter["calls"])
        and row["import_mode"] == "reference"
        and source["local_locator"] == str(reference.resolve())
        and any("gamma-content" in match["text"] for match in search)
        and digest(reference) == before
        and skipped["items"][0]["status"] == "duplicate"
        and added_anyway["items"][0]["status"] == "added"
        and len(duplicate_rows) == 2
        and duplicate_materials[0]["stable_id"] != duplicate_materials[1]["stable_id"]
        and blob_count == 1
    )


def check_snapshot_consistency(tmp: Path) -> bool:
    library = tmp / "snapshot-library"
    server = load_server(tmp / "snapshot-loader", library, tmp / "snapshot-runtime" / "studyhub.sqlite")
    conn = connect(server)
    course = server.manage_course(conn, {"action": "create", "course_code": "TEST7251", "display_name": "Snapshot Consistency"})
    week = server.manage_week(conn, {"action": "create", "course_id": course["id"], "label": "Week 03", "kind": "week"})

    stale = write_text(tmp / "snapshot" / "Stale Identity.txt", "stale-alpha-content")
    identity_a = server.identify_ingestion_path(stale)
    write_text(stale, "stale-beta-content", mtime=1_700_000_200)
    expected_b = digest(stale)
    stale_result = server.ingest_material_candidate(conn, make_candidate(server, stale, course, week, identity=identity_a))
    stale_row = conn.execute("SELECT * FROM files WHERE id=?", (stale_result.legacy_file_id,)).fetchone()
    stale_material = material_for_file(conn, stale_result.legacy_file_id)
    stale_version = active_version(conn, stale_material["id"])
    stale_blob = conn.execute("SELECT * FROM blobs WHERE id=?", (stale_version["blob_id"],)).fetchone()
    stale_search = server.search_local_context(conn, "stale-beta-content", {"fileId": stale_result.legacy_file_id}, limit=3)

    changing = write_text(tmp / "snapshot" / "Changing During Extract.txt", "changing-alpha-content", mtime=1_700_000_300)
    expected_before_change = digest(changing)
    original_extract = server.extract_text
    mutation = {"done": False}

    def mutating_extract(path: Path) -> str:
        if path.name == changing.name and not mutation["done"]:
            mutation["done"] = True
            write_text(path, "changing-beta-content", mtime=1_700_000_400)
            return "changing-beta-content"
        return original_extract(path)

    server.extract_text = mutating_extract
    try:
        changing_result = server.ingest_material_candidate(conn, make_candidate(server, changing, course, week))
    finally:
        server.extract_text = original_extract
    expected_after_change = digest(changing)
    changing_row = conn.execute("SELECT * FROM files WHERE id=?", (changing_result.legacy_file_id,)).fetchone()
    changing_material = material_for_file(conn, changing_result.legacy_file_id)
    changing_version = active_version(conn, changing_material["id"])
    changing_blob = conn.execute("SELECT * FROM blobs WHERE id=?", (changing_version["blob_id"],)).fetchone()
    old_mixed_row = conn.execute("SELECT * FROM files WHERE sha256=?", (expected_before_change,)).fetchone()
    changing_search = server.search_local_context(conn, "changing-beta-content", {"fileId": changing_result.legacy_file_id}, limit=3)
    conn.rollback()
    conn.close()
    return (
        stale_row["sha256"] == expected_b
        and stale_version["sha256"] == expected_b
        and stale_blob["sha256"] == expected_b
        and any("stale-beta-content" in match["text"] for match in stale_search)
        and changing_row["sha256"] == expected_after_change
        and changing_version["sha256"] == expected_after_change
        and changing_blob["sha256"] == expected_after_change
        and old_mixed_row is None
        and any("changing-beta-content" in match["text"] for match in changing_search)
    )


def check_suspicious_and_missing_source(tmp: Path) -> bool:
    library = tmp / "degraded-library"
    bad = write_bytes(
        library / "TEST7301 - Degraded" / "Week 01" / "01 Course Materials" / "Lecture" / "Not A Real PDF.pdf",
        b"<html><body>Synthetic login page saved with a PDF extension.</body></html>\n",
    )
    keep = write_text(
        library / "TEST7301 - Degraded" / "Week 01" / "01 Course Materials" / "Lecture" / "Will Missing.txt",
        "This material will become missing but keep provenance.",
    )
    bad_hash = digest(bad)
    server = load_server(tmp / "degraded-loader", library, tmp / "degraded-runtime" / "studyhub.sqlite")
    first = server.scan_library(library)
    conn = connect(server)
    bad_row = file_by_name(conn, bad.name)
    bad_chunks = scalar(conn, "SELECT COUNT(*) FROM document_chunks WHERE file_id=?", (bad_row["id"],))
    missing_before = file_by_name(conn, keep.name)
    missing_material = material_for_file(conn, missing_before["id"])
    keep.unlink()
    server.scan_library(library)
    missing_after = conn.execute("SELECT * FROM files WHERE id=?", (missing_before["id"],)).fetchone()
    material_after = material_for_file(conn, missing_before["id"])
    historical_versions = conn.execute("SELECT * FROM material_versions WHERE material_id=?", (material_after["id"],)).fetchall()
    conn.close()
    return (
        first.new_files == 2
        and bad_row["suspicious"] == "PDF extension but looks like HTML/login page"
        and bad_chunks == 0
        and digest(bad) == bad_hash
        and missing_after["source_missing"] == 1
        and missing_after["missing_at"]
        and material_after["stable_id"] == missing_material["stable_id"]
        and len(historical_versions) >= 1
    )


def check_batch_import_and_failure_safety(tmp: Path) -> bool:
    library = tmp / "batch-library"
    server = load_server(tmp / "batch-loader", library, tmp / "batch-runtime" / "studyhub.sqlite")
    conn = connect(server)
    folder = tmp / "batch-source" / "TEST7401 - Batch Import"
    for index in range(6):
        write_text(
            folder / "Week 01" / "01 Course Materials" / "Lecture" / f"Batch {index}.txt",
            f"Batch ingestion content {index}.",
        )
    counter = install_ingestion_counter(server)
    original_reconcile = server.reconcile_domain_projection
    reconcile_calls = {"count": 0}

    def counted_reconcile(inner_conn: sqlite3.Connection) -> None:
        reconcile_calls["count"] += 1
        original_reconcile(inner_conn)

    server.reconcile_domain_projection = counted_reconcile
    imported = server.import_course_folder(conn, {"path": str(folder), "display_name": "Batch Import", "is_official": True})
    server.reconcile_domain_projection = original_reconcile
    counter["restore"]()
    stable = write_text(tmp / "failure" / "Stable.txt", "Stable material survives failed ingestion.")
    course = server.manage_course(conn, {"action": "create", "course_code": "TEST7402", "display_name": "Failure Safety"})
    week = server.manage_week(conn, {"action": "create", "course_id": course["id"], "label": "Week 01", "kind": "week"})
    stable_result = server.register_material_paths(
        conn,
        {"paths": [str(stable)], "course_id": course["id"], "week_id": week["id"], "material_type": "reading"},
    )
    stable_id = stable_result["items"][0]["id"]
    stable_hash = digest(stable)
    valid_a = write_text(tmp / "failure" / "Valid A.txt", "Valid A should not be partially committed.")
    failing = write_text(tmp / "failure" / "Failing.txt", "This extraction will fail.")
    original_extract = server.extract_text

    def failing_extract(path: Path) -> str:
        if path.name == "Failing.txt":
            raise RuntimeError("synthetic extraction failure")
        return original_extract(path)

    server.extract_text = failing_extract
    failed_without_damage = False
    try:
        server.register_material_paths(
            conn,
            {"paths": [str(valid_a), str(failing)], "course_id": course["id"], "week_id": week["id"], "material_type": "reading"},
        )
    except RuntimeError:
        conn.rollback()
        stable_row = conn.execute("SELECT * FROM files WHERE id=?", (stable_id,)).fetchone()
        stable_material = material_for_file(conn, stable_id)
        stable_chunks = scalar(conn, "SELECT COUNT(*) FROM document_chunks WHERE file_id=?", (stable_id,))
        stable_text = server.read_cached_text(stable_row, 500)
        valid_a_row = conn.execute("SELECT * FROM files WHERE original_path=?", (str(valid_a.resolve()),)).fetchone()
        failing_row = conn.execute("SELECT * FROM files WHERE original_path=?", (str(failing.resolve()),)).fetchone()
        failed_without_damage = (
            stable_row["active"] == 1
            and stable_row["sha256"] == stable_hash
            and stable_material is not None
            and stable_chunks > 0
            and "survives failed ingestion" in stable_text
            and valid_a_row is None
            and failing_row is None
        )
    finally:
        server.extract_text = original_extract
    conn.close()
    return (
        imported["detected"] == 6
        and imported["added"] == 6
        and len([call for call in counter["calls"] if call["import_mode"] == "reference"]) == 6
        and reconcile_calls["count"] == 1
        and failed_without_damage
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="studyhub-ingestion-core-") as tmp_name:
        tmp = Path(tmp_name)
        checks = {
            "direct_helper_keeps_transaction_open_and_rolls_back_targeted_domain": check_direct_helper_transaction_and_targeted_domain(tmp),
            "scanned_material_uses_ingestion_core_and_projects_domain": check_scanned_and_modified_material(tmp),
            "manual_reference_uses_ingestion_core_and_preserves_duplicates": check_manual_reference_and_duplicates(tmp),
            "source_snapshot_consistency_detects_stale_and_mid_ingest_changes": check_snapshot_consistency(tmp),
            "suspicious_input_and_missing_source_preserve_current_behavior": check_suspicious_and_missing_source(tmp),
            "batch_import_uses_core_once_per_file_without_n_reconciles": check_batch_import_and_failure_safety(tmp),
        }
    for name, ok in checks.items():
        print(f"{name}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
