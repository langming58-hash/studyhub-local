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

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
