#!/usr/bin/env python3
"""Manual Review Cards V1 acceptance for Phase 4."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "bin") not in sys.path:
    sys.path.insert(0, str(ROOT / "bin"))

from backup_restore_preview_acceptance import (  # noqa: E402
    mutate_dataset as mutate_backup_dataset,
    stage as stage_restore,
)
from source_linked_notes_acceptance import (  # noqa: E402
    load_server,
    note_create,
    prepare_target,
    synthetic_pdf,
)


def check(name: str, ok: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if ok else 'FAIL'}")
    if not ok:
        failures.append(name)


def fixed_iso(days: int = 0) -> datetime:
    return datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(days=days)


def decisions_with_card_restore(plan: dict) -> list[dict]:
    result = []
    for section in plan.get("sections", []):
        for action in section.get("actions", []):
            classification = action.get("classification")
            if classification == "CONFLICT":
                decision = "RESTORE_BACKUP" if action.get("label") == "Review card" else "KEEP_LOCAL"
            elif classification in {"RELINK_REQUIRED", "UNRESOLVED_REFERENCE", "UNSUPPORTED_BY_V1"}:
                decision = "SKIP"
            else:
                continue
            result.append({"dataset": action["dataset"], "stable_id": action["stable_id"], "decision": decision})
    return result


def cards_snapshot(conn) -> list[dict]:
    columns = (
        "id", "stable_id", "material_id", "front", "back", "anchor_version",
        "anchor_page", "anchor_material_stable_id", "anchor_version_stable_id",
        "fsrs_state", "fsrs_step", "fsrs_stability", "fsrs_difficulty",
        "fsrs_due_at", "fsrs_last_review_at", "archived", "created_at", "updated_at",
    )
    rows = conn.execute(f"SELECT {', '.join(columns)} FROM review_cards ORDER BY id").fetchall()
    return [{column: row[column] for column in columns} for row in rows]


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="studyhub-cards-") as raw:
        tmp = Path(raw)
        library = tmp / "StudyLibrary"
        database = tmp / "runtime" / "studyhub.sqlite"
        server = load_server(tmp, library, database)

        pdf = library / "TEST1001 - Course" / "Week 01" / "Lecture" / "notes.pdf"
        pdf.parent.mkdir(parents=True)
        pdf_v1 = synthetic_pdf(["Page one text", "Page two text", "Page three text"])
        pdf.write_bytes(pdf_v1)
        before = hashlib.sha256(pdf.read_bytes()).hexdigest()
        server.scan_library(library)

        conn = server.connect_db()
        server.init_db(conn)
        server.run_schema_migrations(conn)
        file_row = conn.execute("SELECT * FROM files WHERE filename='notes.pdf'").fetchone()
        file_id = int(file_row["id"])
        material_stable, version_stable = server.note_anchor_for_file(conn, file_id)

        # Card creation, persistence, and anchor state.
        created = server.create_review_card(
            conn,
            {"materialId": file_id, "front": "What is page one?", "back": "Page one answer", "anchorPage": 1},
        )
        check("card_creation_persists", created["id"] and created["stable_id"].startswith("card_"), failures)
        check("card_anchor_state_ok_for_identical_source", created["anchor_state"] == "ok", failures)
        check("card_has_no_secret_fields", not any(key in created for key in ("sha256", "hash", "path", "locator")), failures)

        rows = server.review_card_rows(conn)
        check("card_list_returns_row", len(rows) == 1 and rows[0]["front"] == "What is page one?", failures)

        # Editing and archiving.
        server.update_review_card(conn, {"id": created["id"], "front": "Edited question", "back": "Edited answer"})
        edited = conn.execute("SELECT front, back FROM review_cards WHERE id=?", (created["id"],)).fetchone()
        check("card_edit_persists", edited["front"] == "Edited question" and edited["back"] == "Edited answer", failures)
        server.set_review_card_archived(conn, {"id": created["id"]}, archived=True)
        check("card_archive_persists", conn.execute("SELECT archived FROM review_cards WHERE id=?", (created["id"],)).fetchone()["archived"] == 1, failures)
        check("archived_card_excluded_from_queue", len(server.review_card_queue(conn)) == 0, failures)
        server.set_review_card_archived(conn, {"id": created["id"]}, archived=False)
        check("card_restore_persists", conn.execute("SELECT archived FROM review_cards WHERE id=?", (created["id"],)).fetchone()["archived"] == 0, failures)

        # Note-to-card anchor reuse.
        note_create(
            server,
            {"targetType": "file", "targetId": file_id, "courseId": file_row["course_id"], "week": file_row["week_label"], "body": "Note for card", "anchorPage": 3, "pageCount": 3},
        )
        from_note = server.create_review_card(
            conn,
            {"materialId": file_id, "front": "Page three?", "back": "From note", "anchorPage": 3},
        )
        check("note_anchor_card_is_ok", from_note["anchor_state"] == "ok" and from_note["anchor_page"] == 3, failures)

        # Independent FSRS scheduling.
        material_before = conn.execute(
            "SELECT fsrs_due_at FROM material_study_state WHERE material_id=?", (server.study_material_row(conn, file_id)["material_id"],)
        ).fetchone()
        reviewed = server.review_review_card(conn, {"id": created["id"], "grade": "good"}, reviewed_at=fixed_iso())
        check("card_review_sets_fsrs_fields", bool(reviewed["fsrs_due_at"]) and bool(reviewed["fsrs_last_review_at"]), failures)
        material_after = conn.execute(
            "SELECT fsrs_due_at FROM material_study_state WHERE material_id=?", (server.study_material_row(conn, file_id)["material_id"],)
        ).fetchone()
        check(
            "card_fsrs_is_independent_of_material_state",
            (material_before["fsrs_due_at"] if material_before else None) == (material_after["fsrs_due_at"] if material_after else None),
            failures,
        )

        # Deterministic transitions on identical fresh cards.
        def fresh_card_due(grade: str) -> str:
            card = server.create_review_card(conn, {"materialId": file_id, "front": grade, "back": grade})
            result = server.review_review_card(conn, {"id": card["id"], "grade": grade}, reviewed_at=fixed_iso())
            return str(result["fsrs_due_at"])

        again_due, hard_due, good_due, easy_due = (fresh_card_due(grade) for grade in ("again", "hard", "good", "easy"))
        check("grade_transitions_are_deterministic_and_ordered", again_due < hard_due < good_due < easy_due, failures)
        again_card = server.create_review_card(conn, {"materialId": file_id, "front": "again-dup", "back": ""})
        again_dup = server.review_review_card(conn, {"id": again_card["id"], "grade": "again"}, reviewed_at=fixed_iso())
        check("same_grade_same_time_deterministic", str(again_dup["fsrs_due_at"]) == again_due, failures)

        # Queue ordering by due time.
        queue_due = {
            "again": "2026-05-01T12:00:00+00:00",
            "again-dup": "2026-05-01T13:00:00+00:00",
            "good": "2026-05-02T12:00:00+00:00",
            "hard": "2026-05-03T12:00:00+00:00",
            "easy": "2026-05-04T12:00:00+00:00",
        }
        for front, due in queue_due.items():
            conn.execute("UPDATE review_cards SET fsrs_due_at=?, archived=0 WHERE front=?", (due, front))
        conn.execute("UPDATE review_cards SET fsrs_due_at='2026-05-05T12:00:00+00:00', archived=0 WHERE id=?", (created["id"],))
        conn.commit()
        queue = server.review_card_queue(conn, at=fixed_iso(10))
        queue_fronts = [item["front"] for item in queue]
        check("queue_orders_oldest_due_first", queue_fronts[:3] == ["again", "again-dup", "good"], failures)

        # Stale and missing source safety.
        conn.execute(
            "UPDATE review_cards SET anchor_material_stable_id=?, anchor_version_stable_id=? WHERE id=?",
            (material_stable, version_stable, created["id"]),
        )
        conn.commit()
        stale_pdf = synthetic_pdf(["Changed one", "Changed two", "Changed three"])
        pdf.write_bytes(stale_pdf)
        server.scan_library(library)
        stale_state = server.review_card_anchor_state(conn, conn.execute("SELECT * FROM review_cards WHERE id=?", (created["id"],)).fetchone())
        check("changed_source_reports_stale", stale_state == "stale", failures)
        conn.execute("UPDATE review_cards SET anchor_material_stable_id='mat_missing' WHERE id=?", (created["id"],))
        conn.commit()
        missing_row = conn.execute("SELECT * FROM review_cards WHERE id=?", (created["id"],)).fetchone()
        check("missing_source_reports_missing", server.review_card_anchor_state(conn, missing_row) == "missing", failures)

        # Invalid inputs.
        for bad_page in (0, -1, 2.5, True, "2"):
            try:
                server.create_review_card(conn, {"materialId": file_id, "front": "bad", "back": "", "anchorPage": bad_page})
                check(f"invalid_anchor_{bad_page}_rejected", False, failures)
            except ValueError:
                check(f"invalid_anchor_{bad_page}_rejected", True, failures)
        for bad_grade in ("nope", "", 3):
            try:
                server.review_review_card(conn, {"id": created["id"], "grade": bad_grade}, reviewed_at=fixed_iso())
                check(f"invalid_grade_{bad_grade}_rejected", False, failures)
            except ValueError:
                check(f"invalid_grade_{bad_grade}_rejected", True, failures)
        try:
            server.create_review_card(conn, {"materialId": file_id, "front": "", "back": ""})
            check("empty_question_rejected", False, failures)
        except ValueError:
            check("empty_question_rejected", True, failures)

        check("original_pdf_matches_rescanned_content", hashlib.sha256(pdf.read_bytes()).hexdigest() == hashlib.sha256(stale_pdf).hexdigest(), failures)
        check("original_pdf_content_changed_from_v1", hashlib.sha256(pdf.read_bytes()).hexdigest() != before, failures)

        current_card = server.create_review_card(
            conn,
            {"materialId": file_id, "front": "Current version?", "back": "Current answer", "anchorPage": 2},
        )
        check("current_version_card_reports_ok", current_card["anchor_state"] == "ok", failures)

        # Backup round-trip into an isolated workspace.
        archive = server.export_current_profile_backup()
        archive_path = server.EXPORT_DIR / archive["filename"]
        with zipfile.ZipFile(archive_path) as zf:
            study = json.loads(zf.read("data/study_records.json").decode("utf-8"))
        card_records = [record for record in study["records"] if record["record_type"] == "review_card"]
        check("backup_exports_review_cards", bool(card_records) and all(record.get("material_ref") for record in card_records), failures)
        archive_text = json.dumps(study)
        check("archive_has_no_absolute_paths", str(tmp) not in archive_text and "/Users/" not in archive_text, failures)

        target, tconn, target_tmp = prepare_target("cards", pdf.read_bytes())
        target_file = tconn.execute("SELECT id FROM files WHERE filename='notes.pdf'").fetchone()
        stage_restore(target, archive_path, "a" * 32)
        plan = target.preview_staged_restore("a" * 32)
        result = target.apply_staged_restore("a" * 32, plan["plan_revision"], decisions_with_card_restore(plan))
        check("restore_applies_cards", result.get("ok") is True, failures)
        restored_cards = target.review_card_rows(tconn)
        check("restore_preserves_card_count", len(restored_cards) == len(card_records), failures)
        restored_rows = tconn.execute(
            "SELECT card.*, material.legacy_file_id FROM review_cards card JOIN materials material ON material.id=card.material_id"
        ).fetchall()
        restored_by_state = {}
        for row in restored_rows:
            restored_by_state.setdefault(target.review_card_anchor_state(tconn, row), []).append(row["front"])
        check("restore_keeps_current_anchor_ok", "Current version?" in restored_by_state.get("ok", []), failures)
        check("restore_keeps_stale_anchor_stale", "Edited question" in restored_by_state.get("stale", []), failures)
        check("restore_keeps_unanchored_unset", bool(restored_by_state.get("unset", [])), failures)

        # Restore conflict + exact Undo.
        first_card_record = card_records[0]
        tconn.execute(
            "UPDATE review_cards SET front='Prior local front' WHERE stable_id=?",
            (first_card_record["stable_id"],),
        )
        tconn.commit()
        prior_cards = cards_snapshot(tconn)
        plan2 = target.preview_staged_restore("a" * 32)
        apply2 = target.apply_staged_restore("a" * 32, plan2["plan_revision"], decisions_with_card_restore(plan2))
        operation_id = apply2["applied"]["restore_operation_id"]
        check(
            "restore_backup_applies_card_conflict",
            tconn.execute("SELECT front FROM review_cards WHERE stable_id=?", (first_card_record["stable_id"],)).fetchone()["front"] == first_card_record["front"],
            failures,
        )
        undo_preview = target.preview_restore_undo(operation_id)
        undo_result = target.apply_restore_undo(operation_id, undo_preview["undo_revision"])
        check("undo_restores_exact_card_state", undo_result.get("ok") is True and cards_snapshot(tconn) == prior_cards, failures)
        tconn.close()
        shutil.rmtree(target_tmp, ignore_errors=True)

        # Old archives without review-card records remain valid and readable.
        def strip_cards(payload):
            payload["records"] = [record for record in payload["records"] if record.get("record_type") != "review_card"]

        legacy_path = mutate_backup_dataset(server, archive_path, "data/study_records.json", strip_cards)
        try:
            server.validate_backup_archive(legacy_path)
            check("legacy_archive_without_cards_valid", True, failures)
        except ValueError:
            check("legacy_archive_without_cards_valid", False, failures)

        # Profile isolation.
        other_profile = Path(tempfile.mkdtemp(prefix="studyhub-cards-other-"))
        other_library = other_profile / "StudyLibrary"
        other_db = other_profile / "runtime" / "studyhub.sqlite"
        other = load_server(other_profile, other_library, other_db)
        other_pdf = other_library / "TEST1001 - Course" / "Week 01" / "Lecture" / "notes.pdf"
        other_pdf.parent.mkdir(parents=True)
        other_pdf.write_bytes(pdf.read_bytes())
        other.scan_library(other_library)
        other_conn = other.connect_db()
        other.init_db(other_conn)
        other.run_schema_migrations(other_conn)
        check("profile_isolation_no_shared_cards", len(other.review_card_rows(other_conn)) == 0, failures)
        other_conn.close()
        shutil.rmtree(other_profile, ignore_errors=True)

        conn.close()

    if failures:
        print("Manual review cards acceptance: FAIL")
        print("Failures: " + ", ".join(failures))
        return 1
    print("Manual review cards acceptance: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
