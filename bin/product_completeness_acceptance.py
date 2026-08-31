#!/usr/bin/env python3
"""Synthetic P0 acceptance checks for StudyHub product completeness."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_server(tmp: Path):
    os.environ.pop("DEMO_MODE", None)
    os.environ["STUDY_LIBRARY_PATH"] = str(tmp / "Managed Library")
    os.environ["STUDYHUB_RUNTIME_DIR"] = str(tmp / "runtime")
    os.environ["DATABASE_PATH"] = str(tmp / "runtime" / "data" / "studyhub.sqlite")
    os.environ["STUDYHUB_CONFIG_PATH"] = str(tmp / "runtime" / "settings.env")
    os.environ["STUDYHUB_DESKTOP"] = "true"
    spec = importlib.util.spec_from_file_location("studyhub_product_completeness_test", ROOT / "server.py")
    server = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    return server


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_legacy_empty_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
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
        """
    )
    conn.commit()
    conn.close()


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        server = load_server(tmp)
        server.DEFAULT_STUDY_ROOT.mkdir(parents=True)

        legacy_db = tmp / "legacy.sqlite"
        create_legacy_empty_database(legacy_db)
        original_db = server.DB_PATH
        server.DB_PATH = legacy_db
        legacy_conn = server.connect_db()
        server.init_db(legacy_conn)
        course_columns = {row["name"] for row in legacy_conn.execute("PRAGMA table_info(courses)")}
        file_columns = {row["name"] for row in legacy_conn.execute("PRAGMA table_info(files)")}
        legacy_conn.close()
        server.DB_PATH = original_db

        source = tmp / "TEST2001 - Synthetic Source Course"
        originals: list[Path] = []
        for index in range(1, 23):
            unit = "Module Alpha" if index <= 11 else "Week 02"
            material_type = "Lecture" if index % 2 else "Tutorial"
            path = source / unit / material_type / f"TEST2001 {material_type} {index:02d}.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"TEST2001 synthetic source {index}. Grounded phrase alpha-{index}.\n", encoding="utf-8")
            originals.append(path)
        duplicate = source / "Week 02" / "Lecture" / "TEST2001 Duplicate.txt"
        duplicate.write_bytes(originals[0].read_bytes())
        originals.append(duplicate)
        before = {str(path): digest(path) for path in originals}

        conn = server.connect_db()
        server.init_db(conn)
        term = server.manage_term(conn, {"action": "create", "name": "Semester TEST"})
        renamed_term = server.manage_term(conn, {"action": "rename", "id": term["id"], "name": "Semester TEST Renamed"})
        server.manage_term(conn, {"action": "archive", "id": term["id"]})
        restored_term = server.manage_term(conn, {"action": "restore", "id": term["id"]})
        imported = server.import_course_folder(
            conn,
            {"path": str(source), "term_id": term["id"], "is_official": True},
        )
        course_id = int(imported["course"]["id"])
        course_stable_id = imported["course"]["stable_id"]
        updated_course = server.manage_course(
            conn,
            {"action": "update", "id": course_id, "course_code": "TEST2099", "display_name": "Renamed Synthetic Course", "term_id": term["id"]},
        )
        second_course = server.manage_course(
            conn,
            {"action": "create", "course_code": "TEST3001", "display_name": "Move Destination", "term_id": term["id"]},
        )
        second_week = server.manage_week(
            conn,
            {"action": "create", "course_id": second_course["id"], "label": "Module Beta", "kind": "module"},
        )
        empty_week = server.manage_week(
            conn,
            {"action": "create", "course_id": course_id, "label": "Week 07", "kind": "week"},
        )
        renamed_week = server.manage_week(
            conn,
            {"action": "rename", "id": empty_week["id"], "label": "Review Block", "kind": "module"},
        )
        server.manage_week(conn, {"action": "remove", "id": empty_week["id"]})
        removed_week = conn.execute("SELECT removed_at FROM weeks WHERE id=?", (empty_week["id"],)).fetchone()["removed_at"]
        weeks = conn.execute(
            "SELECT * FROM weeks WHERE course_id=? AND removed_at IS NULL ORDER BY week_label",
            (course_id,),
        ).fetchall()
        files = conn.execute(
            "SELECT * FROM files WHERE course_id=? AND active=1 ORDER BY id",
            (course_id,),
        ).fetchall()
        first = files[0]
        first_id = int(first["id"])
        first_stable_id = first["stable_id"]
        conn.execute(
            "INSERT INTO notes(target_type, target_id, course_id, week_label, body, created_at, updated_at) VALUES ('file', ?, ?, ?, 'Synthetic note', ?, ?)",
            (first_id, course_id, first["week_label"], server.now_iso(), server.now_iso()),
        )
        conn.execute("INSERT INTO stars(target_type, target_id, created_at) VALUES ('file', ?, ?)", (first_id, server.now_iso()))
        conn.commit()
        target_week = weeks[-1]
        server.manage_materials(
            conn,
            {
                "action": "move",
                "ids": [first_id],
                "course_id": second_course["id"],
                "week_id": second_week["id"],
                "material_type": "Reading",
            },
        )
        moved_between_courses = conn.execute("SELECT course_id, stable_id FROM files WHERE id=?", (first_id,)).fetchone()
        server.manage_materials(
            conn,
            {
                "action": "classify",
                "ids": [first_id],
                "course_id": course_id,
                "week_id": target_week["id"],
                "material_type": "Reading",
            },
        )
        server.manage_materials(conn, {"action": "rename", "id": first_id, "display_name": "Synthetic renamed material"})
        renamed = conn.execute("SELECT * FROM files WHERE id=?", (first_id,)).fetchone()
        association_count = conn.execute(
            "SELECT (SELECT COUNT(*) FROM notes WHERE target_id=?) + (SELECT COUNT(*) FROM stars WHERE target_id=?) AS c",
            (first_id, first_id),
        ).fetchone()["c"]

        missing_source = Path(renamed["original_path"])
        moved_source = missing_source.with_name("TEST2001 Relinked.txt")
        missing_source.rename(moved_source)
        server.scan_library(server.DEFAULT_STUDY_ROOT)
        missing_flag = conn.execute("SELECT source_missing FROM files WHERE id=?", (first_id,)).fetchone()["source_missing"]
        server.manage_materials(conn, {"action": "relink", "id": first_id, "path": str(moved_source)})
        relinked = conn.execute("SELECT * FROM files WHERE id=?", (first_id,)).fetchone()
        server.clear_recreatable_cache(conn)
        cache_text = server.read_cached_text(conn.execute("SELECT * FROM files WHERE id=?", (first_id,)).fetchone(), 500)
        public_payload = server.public_file(relinked)

        server.manage_course(conn, {"action": "archive", "id": course_id})
        archived = conn.execute("SELECT archived FROM courses WHERE id=?", (course_id,)).fetchone()["archived"]
        server.manage_course(conn, {"action": "restore", "id": course_id})
        restored = conn.execute("SELECT stable_id, archived FROM courses WHERE id=?", (course_id,)).fetchone()
        server.manage_course(conn, {"action": "remove", "id": second_course["id"]})
        removed_course = conn.execute("SELECT removed_at FROM courses WHERE id=?", (second_course["id"],)).fetchone()["removed_at"]
        server.manage_materials(conn, {"action": "remove", "id": first_id})
        removed = conn.execute("SELECT active, removed_at FROM files WHERE id=?", (first_id,)).fetchone()
        after_management = {str(path): digest(path) for path in originals if path.exists()}
        after_management[str(moved_source)] = digest(moved_source)

        reset_before = digest(moved_source)
        conn.close()
        conn = server.connect_db()
        server.init_db(conn)
        persisted_course = conn.execute("SELECT stable_id, course_code, display_name FROM courses WHERE id=?", (course_id,)).fetchone()
        persisted_note_count = conn.execute("SELECT COUNT(*) AS c FROM notes WHERE target_id=?", (first_id,)).fetchone()["c"]
        server.reset_studyhub_state(conn)
        reset_after = digest(moved_source)
        db_file_count = conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()["c"]
        conn.close()

        app_js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        index_html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        tauri_source = (ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
        capability = (ROOT / "src-tauri" / "capabilities" / "default.json").read_text(encoding="utf-8")
        after_by_content = sorted(after_management.values())
        before_by_content = sorted(before.values())

        checks = {
            "legacy_schema_migrates_additively": {"stable_id", "term_id", "archived", "source_kind"} <= course_columns
            and {"stable_id", "material_type", "import_mode", "source_missing", "removed_at", "material_created_at", "material_updated_at"} <= file_columns,
            "term_crud_and_archive": renamed_term["name"] == "Semester TEST Renamed" and restored_term["archived"] == 0,
            "term_course_folder_import": imported["detected"] == 23 and imported["added"] == 22 and imported["duplicates"] == 1,
            "dynamic_week_and_module_detection": {row["week_label"] for row in weeks} == {"Module Alpha", "Week 02"}
            and not any(row["week_label"] == "Week 12" for row in weeks),
            "dynamic_week_rename_and_remove_empty": renamed_week["week_label"] == "Review Block" and renamed_week["kind"] == "module" and removed_week,
            "course_rename_and_code_change": updated_course["name"] == "Renamed Synthetic Course" and updated_course["code"] == "TEST2099",
            "stable_ids_survive_metadata_changes": renamed["stable_id"] == first_stable_id and restored["stable_id"] == course_stable_id,
            "move_between_courses_preserves_material_id": moved_between_courses["course_id"] == second_course["id"] and moved_between_courses["stable_id"] == first_stable_id,
            "notes_and_stars_keep_file_identity": association_count == 2,
            "classification_and_display_rename_are_metadata_only": renamed["material_type"] == "reading"
            and renamed["display_name"] == "Synthetic renamed material"
            and renamed["filename"] != renamed["display_name"],
            "missing_file_and_relink": missing_flag == 1 and relinked["source_missing"] == 0 and Path(relinked["original_path"]) == moved_source,
            "duplicate_detection_by_hash": len(files) == 22,
            "cache_rebuild_from_reference": "Grounded phrase" in cache_text,
            "public_payload_hides_absolute_path": "original_path" not in public_payload and str(tmp) not in str(public_payload),
            "archive_restore_preserves_course": archived == 1 and restored["archived"] == 0,
            "remove_course_is_metadata_only": bool(removed_course),
            "restart_persists_metadata_and_notes": persisted_course["stable_id"] == course_stable_id
            and persisted_course["course_code"] == "TEST2099"
            and persisted_note_count == 1,
            "remove_is_metadata_only": removed["active"] == 0 and removed["removed_at"] and moved_source.exists(),
            "original_checksums_unchanged": before_by_content == after_by_content,
            "reset_keeps_original_files": reset_before == reset_after and db_file_count == 0,
            "first_run_has_clean_clear_choices": "Try Demo" not in app_js
            and "data-create-first-course" in app_js
            and "data-import-first-folder" in app_js,
            "native_multi_file_picker_and_drop": "blocking_pick_files" in tauri_source
            and "tauri://drag-drop" in app_js
            and "allow-choose-study-files" in capability,
            "material_type_is_structured": 'id="uploadMaterialType"' in index_html and "batchMaterialType" in app_js,
            "selection_controls_are_week_scoped": ".map(fileCard)" not in app_js
            and "rows.map((file) => fileCard(file, true))" in app_js,
            "legacy_demo_provenance_is_hidden": server.course_visibility_sql().startswith("c.source_kind!='demo'")
            and "visible_term.archived=0" in server.course_visibility_sql(),
        }
        for name, ok in checks.items():
            print(f"{name}: {'PASS' if ok else 'FAIL'}")
        return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
