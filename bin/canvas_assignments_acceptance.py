#!/usr/bin/env python3
"""Canvas assignment read-sync, Today bucketing, and scope-gating acceptance."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server
from server import (
    CanvasAssignmentFact,
    CanvasConnector,
    CanvasConnectorError,
    refresh_canvas_assignments,
    today_view,
)


def check(name: str, ok: bool, failures: list[str]) -> None:
    print(f"{name}: {'PASS' if ok else 'FAIL'}")
    if not ok:
        failures.append(name)


class FakeHTTP:
    def __init__(self, responses: list[tuple[int, str, dict]]):
        self.responses = list(responses)
        self.requests: list[tuple[str, str, dict]] = []

    def __call__(self, url: str, headers: dict, timeout: int = 10):
        self.requests.append((url, headers.get("Authorization", ""), timeout))
        status, body, extra = self.responses.pop(0)
        return server.CanvasHTTPResponse(status, {"Content-Type": "application/json", **extra}, json.dumps(body).encode("utf-8"))


def record(origin: str = "https://canvas.example.edu") -> server.CanvasConnectionRecord:
    return server.CanvasConnectionRecord(origin, "synthetic-token")


def assignment_payload(assignment_id: str, name: str, due: str | None) -> dict:
    return {
        "id": assignment_id,
        "name": name,
        "due_at": due,
        "unlock_at": None,
        "lock_at": None,
        "html_url": f"https://canvas.example.edu/courses/1/assignments/{assignment_id}",
        "published": True,
        "submission_types": ["online_upload"],
        "submission": {"workflow_state": "unsubmitted", "submitted_at": None},
        "updated_at": "2026-11-04T00:00:00Z",
    }


def main() -> int:
    failures: list[str] = []

    http = FakeHTTP([(200, [assignment_payload("a1", "Synthetic Assignment", "2026-11-04T03:00:00Z")], {})])
    connector = CanvasConnector(record(), http_get=http)
    facts = connector.course_assignments("1")
    check("assignment_endpoint_exact", http.requests[0][0] == "https://canvas.example.edu/api/v1/courses/1/assignments?include%5B%5D=submission&per_page=100", failures)
    check("assignment_authorization_only", http.requests[0][1] == "Bearer synthetic-token", failures)
    check("assignment_normalized", len(facts) == 1 and facts[0].remote_assignment_stable_id == "a1" and facts[0].name == "Synthetic Assignment", failures)
    check("assignment_remote_id_not_in_html_url", facts[0].html_url == "https://canvas.example.edu/courses/1/assignments/a1", failures)

    cross_http = FakeHTTP([(200, [], {"Link": "<https://evil.example/x>; rel=\"next\""})])
    cross = CanvasConnector(record(), http_get=cross_http)
    try:
        cross.course_assignments("1")
        check("cross_origin_assignment_pagination_denied", False, failures)
    except CanvasConnectorError as exc:
        check("cross_origin_assignment_pagination_denied", exc.code == "cross_origin_pagination", failures)

    with tempfile.TemporaryDirectory(prefix="studyhub-assignments-") as raw:
        db_path = Path(raw) / "test.sqlite"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        server.run_schema_migrations(conn)
        conn.commit()
        server.init_db(conn)

        authority = server.canvas_authority_id("https://canvas.example.edu")
        now_iso = server.now_iso()
        conn.execute(
            "INSERT INTO course_offerings(provider, authority_id, stable_id, remote_course_id, remote_name, remote_course_code, workflow_state, first_seen_at, last_seen_at, created_at, updated_at) VALUES ('canvas', ?, ?, '1', 'Synthetic Course', 'TEST1001', 'available', ?, ?, ?, ?)",
            (authority, "offering-stable-1", now_iso, now_iso, now_iso, now_iso),
        )
        offering_id = conn.execute("SELECT id FROM course_offerings WHERE stable_id='offering-stable-1'").fetchone()["id"]
        conn.execute(
            "INSERT INTO course_offering_selections(offering_id, selected, updated_at) VALUES (?, 1, ?)",
            (offering_id, now_iso),
        )
        conn.commit()

        first_facts = [
            CanvasAssignmentFact("a1", "1", "Alpha", "2026-11-04T03:00:00Z", None, None, "https://canvas.example.edu/a1", True, ("online_upload",), "unsubmitted", None, "2026-11-04T00:00:00Z"),
            CanvasAssignmentFact("a2", "1", "Beta", "2026-11-05T03:00:00Z", None, None, "https://canvas.example.edu/a2", True, ("online_upload",), "unsubmitted", None, "2026-11-04T00:00:00Z"),
        ]

        class FakeConnector:
            record = record()
            def __init__(self, facts):
                self.facts = facts
            def course_assignments(self, remote_course_id):
                return self.facts

        result = refresh_canvas_assignments(conn, FakeConnector(first_facts), authority)
        conn.commit()
        check("refresh_adds_two_events", result["summary"]["added"] == 2, failures)

        second_facts = [
            CanvasAssignmentFact("a1", "1", "Alpha", "2026-11-06T03:00:00Z", None, None, "https://canvas.example.edu/a1", True, ("online_upload",), "unsubmitted", None, "2026-11-04T01:00:00Z"),
        ]
        refresh_canvas_assignments(conn, FakeConnector(second_facts), authority)
        conn.commit()
        rows = conn.execute("SELECT * FROM academic_events ORDER BY remote_event_key").fetchall()
        a1 = next(r for r in rows if r["remote_event_key"] == "a1")
        a2 = next(r for r in rows if r["remote_event_key"] == "a2")
        check("deadline_change_same_identity", a1["due_at_utc"] == "2026-11-06T03:00:00Z", failures)
        check("missing_is_advisory_not_deleted", a2["discovery_state"] == "MISSING_FROM_REMOTE_DISCOVERY", failures)
        deadline_events = conn.execute("SELECT COUNT(*) AS n FROM academic_change_events WHERE event_kind='ASSIGNMENT_DEADLINE_CHANGED'").fetchone()["n"]
        check("deadline_change_journal_written", deadline_events >= 1, failures)

        now = datetime(2026, 11, 4, 2, 0, 0, tzinfo=timezone.utc)
        conn.execute(
            "INSERT INTO academic_events(stable_id, authority_id, course_offering_id, remote_event_key, kind, title, due_at_utc, submission_state, remote_fingerprint, discovery_state, first_seen_at, last_seen_at) VALUES (?, ?, ?, 'due-today', 'ASSIGNMENT_DUE', 'Due Today', ?, 'unsubmitted', 'fp', 'ACTIVE', ?, ?)",
            ("stable-due-today", authority, offering_id, "2026-11-04T05:00:00Z", server.now_iso(), server.now_iso()),
        )
        conn.commit()
        today = today_view(conn, at=now)
        check("today_buckets_due_today", len(today["sections"]["due_today"]) >= 1, failures)
        check("today_no_remote_ids_in_summary", "remote_event_key" not in json.dumps(today["summary"]), failures)
        conn.execute(
            "INSERT INTO academic_events(stable_id, authority_id, course_offering_id, remote_event_key, kind, title, due_at_utc, submission_state, remote_fingerprint, discovery_state, first_seen_at, last_seen_at) VALUES (?, ?, ?, 'pending', 'ASSIGNMENT_DUE', 'Pending Review', ?, 'pending_review', 'fp2', 'ACTIVE', ?, ?)",
            ("stable-pending", authority, offering_id, "2026-11-04T04:00:00Z", server.now_iso(), server.now_iso()),
        )
        conn.commit()
        today_with_pending = today_view(conn, at=now)
        check("today_pending_review_not_actionable", all(item["stable_id"] != "stable-pending" for item in today_with_pending["assignments"]), failures)
        conn.execute("UPDATE course_offering_selections SET selected=0 WHERE offering_id=?", (offering_id,))
        conn.commit()
        deselected = today_view(conn, at=now)
        check("today_deselected_course_excluded", all(item["course_offering_id"] != offering_id for item in deselected["assignments"]), failures)
        check("deselected_event_retained", conn.execute("SELECT COUNT(*) FROM academic_events WHERE course_offering_id=?", (offering_id,)).fetchone()[0] >= 1, failures)
        conn.close()

    source = (ROOT / "server.py").read_text(encoding="utf-8")
    check(
        "scope_gate_before_assignment_http",
        "canvas_scope_set_from_private_transport()" in source
        and "canvas_assignments_scope_required" in source
        and "course_assignments" in source,
        failures,
    )
    check(
        "assignments_endpoint_is_get_only",
        "method=\"GET\"" in source.split("class CanvasConnector", 1)[1].split("def header_value", 1)[0],
        failures,
    )

    # Timezone matrix: canonical UTC storage then local bucketing.
    sydney = ZoneInfo("Australia/Sydney")
    new_york = ZoneInfo("America/New_York")
    at_sydney = datetime(2026, 11, 4, 0, 0, 0, tzinfo=sydney)
    check(
        "timezone_sydney_buckets_local_date",
        server.bucket_deadline(datetime(2026, 11, 4, 0, 30, 0, tzinfo=sydney), at_sydney) == "due_today",
        failures,
    )
    check(
        "timezone_utc_date_differs_from_local",
        datetime(2026, 11, 4, 0, 30, 0, tzinfo=sydney).astimezone(timezone.utc).date()
        != datetime(2026, 11, 4, 0, 30, 0, tzinfo=sydney).date(),
        failures,
    )
    check(
        "timezone_dst_transition_bucket",
        server.bucket_deadline(datetime(2026, 11, 2, 10, 0, 0, tzinfo=new_york), datetime(2026, 11, 2, 9, 0, 0, tzinfo=new_york)) == "due_today",
        failures,
    )
    # Fingerprint covers semantic fields even when updated_at is unchanged.
    base_fact = CanvasAssignmentFact("a1", "1", "Alpha", "2026-11-04T03:00:00+00:00", None, None, "https://canvas.example.edu/a1", True, ("online_upload",), "unsubmitted", None, "2026-11-04T00:00:00+00:00")
    title_changed = CanvasAssignmentFact("a1", "1", "Alpha Renamed", base_fact.due_at, None, None, base_fact.html_url, True, ("online_upload",), "unsubmitted", None, base_fact.remote_updated_at)
    check(
        "fingerprint_changes_on_title_with_same_updated_at",
        server.canvas_assignment_fingerprint(base_fact) != server.canvas_assignment_fingerprint(title_changed),
        failures,
    )

    # Migration v7 -> v8 upgrade and rollback safety.
    with tempfile.TemporaryDirectory(prefix="studyhub-migrate-") as raw2:
        conn = sqlite3.connect(Path(raw2) / "m.sqlite")
        conn.row_factory = sqlite3.Row
        for version, _name, migration in server.SCHEMA_MIGRATIONS:
            if version >= 8:
                break
            server.apply_schema_migration(conn, version, f"m{version}", migration)
        conn.commit()
        check("v8_absent_before_upgrade", conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='academic_events'").fetchone()[0] == 0, failures)
        server.apply_schema_migration(conn, 8, "phase3_academic_events", server.phase3_academic_events_migration)
        conn.commit()
        check("v8_creates_academic_events", conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='academic_events'").fetchone()[0] == 1, failures)
        check("v8_html_url_column", "html_url" in [row[1] for row in conn.execute("PRAGMA table_info(academic_events)")], failures)
        # Rollback safety: a synthetic failure after DDL must not record v8.
        conn2 = sqlite3.connect(Path(raw2) / "r.sqlite")
        conn2.row_factory = sqlite3.Row
        for version, _name, migration in server.SCHEMA_MIGRATIONS:
            if version >= 8:
                break
            server.apply_schema_migration(conn2, version, f"m{version}", migration)
        conn2.commit()
        conn2.execute("SAVEPOINT sp")
        server.phase3_academic_events_migration(conn2)
        conn2.execute("ROLLBACK TO SAVEPOINT sp")
        conn2.execute("RELEASE SAVEPOINT sp")
        conn2.commit()
        check("v8_rollback_leaves_no_table", conn2.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='academic_events'").fetchone()[0] == 0, failures)
        conn.close()
        conn2.close()

    # A/B: partial pagination and multi-course partial fetch must be all-or-nothing.
    with tempfile.TemporaryDirectory(prefix="studyhub-partial-") as raw3:
        conn = sqlite3.connect(Path(raw3) / "p.sqlite")
        conn.row_factory = sqlite3.Row
        server.run_schema_migrations(conn)
        conn.commit()
        server.init_db(conn)
        authority = server.canvas_authority_id("https://canvas.example.edu")
        now_iso = server.now_iso()
        for sid, rid, code in (("off-a", "1", "TESTA"), ("off-b", "2", "TESTB")):
            conn.execute(
                "INSERT INTO course_offerings(provider, authority_id, stable_id, remote_course_id, remote_name, remote_course_code, workflow_state, first_seen_at, last_seen_at, created_at, updated_at) VALUES ('canvas', ?, ?, ?, ?, ?, 'available', ?, ?, ?, ?)",
                (authority, sid, rid, f"Course {code}", code, now_iso, now_iso, now_iso, now_iso),
            )
            off = conn.execute("SELECT id FROM course_offerings WHERE stable_id=?", (sid,)).fetchone()["id"]
            conn.execute("INSERT INTO course_offering_selections(offering_id, selected, updated_at) VALUES (?, 1, ?)", (off, now_iso))
        conn.commit()

        class RaisingConnector:
            record = record()
            def course_assignments(self, remote_course_id):
                raise server.CanvasConnectorError("canvas_unavailable")

        before_rows = conn.execute("SELECT COUNT(*) AS n FROM academic_events").fetchone()["n"]
        before_refresh = conn.execute("SELECT last_refreshed_at FROM canvas_assignment_refresh_state WHERE authority_id=?", (authority,)).fetchone()
        try:
            refresh_canvas_assignments(conn, RaisingConnector(), authority)
            check("partial_pagination_failure_raises", False, failures)
        except server.CanvasConnectorError:
            check("partial_pagination_failure_raises", True, failures)
        conn.commit()
        check("partial_pagination_no_reconciliation", conn.execute("SELECT COUNT(*) AS n FROM academic_events").fetchone()["n"] == before_rows, failures)
        after_refresh = conn.execute("SELECT last_refreshed_at FROM canvas_assignment_refresh_state WHERE authority_id=?", (authority,)).fetchone()
        check("partial_pagination_refresh_timestamp_unchanged", (before_refresh is None and after_refresh is None) or (before_refresh and after_refresh and before_refresh["last_refreshed_at"] == after_refresh["last_refreshed_at"]), failures)

        class PartialConnector:
            record = record()
            def course_assignments(self, remote_course_id):
                if remote_course_id == "1":
                    return [CanvasAssignmentFact("a1", "1", "Alpha", "2026-11-04T03:00:00Z", None, None, "https://canvas.example.edu/a1", True, ("online_upload",), "unsubmitted", None, "2026-11-04T00:00:00Z")]
                raise server.CanvasConnectorError("canvas_unavailable")

        try:
            refresh_canvas_assignments(conn, PartialConnector(), authority)
            check("multi_course_partial_fetch_raises", False, failures)
        except server.CanvasConnectorError:
            check("multi_course_partial_fetch_raises", True, failures)
        conn.commit()
        check("multi_course_partial_no_reconciliation", conn.execute("SELECT COUNT(*) AS n FROM academic_events").fetchone()["n"] == before_rows, failures)
        check("multi_course_partial_no_journal", conn.execute("SELECT COUNT(*) AS n FROM academic_change_events WHERE event_kind IN ('ASSIGNMENT_ADDED','ASSIGNMENT_DEADLINE_CHANGED')").fetchone()["n"] == 0, failures)
        conn.close()

    # D: same-second deadline revisions produce distinct deterministic journal identity.
    check(
        "journal_identity_is_fingerprint_not_clock",
        server.assignment_change_event_stable_id("ASSIGNMENT_DEADLINE_CHANGED", "auth", "off", "a1", "fp-1")
        != server.assignment_change_event_stable_id("ASSIGNMENT_DEADLINE_CHANGED", "auth", "off", "a1", "fp-2")
        and server.assignment_change_event_stable_id("ASSIGNMENT_DEADLINE_CHANGED", "auth", "off", "a1", "fp-1")
        == server.assignment_change_event_stable_id("ASSIGNMENT_DEADLINE_CHANGED", "auth", "off", "a1", "fp-1"),
        failures,
    )

    # E: New York local midnight and UTC/local-date difference.
    ny_midnight = datetime(2026, 11, 4, 0, 0, 0, tzinfo=new_york)
    ny_just_after = datetime(2026, 11, 4, 0, 5, 0, tzinfo=new_york)
    check("timezone_new_york_midnight_due_today", server.bucket_deadline(ny_just_after, ny_midnight) == "due_today", failures)
    ny_late = datetime(2026, 11, 4, 23, 0, 0, tzinfo=new_york)
    check("timezone_new_york_utc_date_differs", ny_late.astimezone(timezone.utc).date() != ny_late.date(), failures)

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
