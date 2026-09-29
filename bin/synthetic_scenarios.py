#!/usr/bin/env python3
"""Deterministic synthetic StudyHub scenario builder.

This module creates current-state academic library scenarios in temporary
directories. It intentionally does not model target-only domain concepts such
as Canvas sync, Source/Blob migrations, or background jobs.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "synthetic-courses"
FIXED_MTIME = 1_700_000_000


@dataclass(frozen=True)
class SyntheticScenario:
    name: str
    root: Path
    library: Path
    database: Path
    runtime: Path
    expectations: dict[str, Any] = field(default_factory=dict)


EXECUTABLE_SCENARIOS: tuple[str, ...] = (
    "clean_empty",
    "normal_small",
    "empty_course",
    "long_metadata",
    "large_library",
    "missing_original",
    "duplicate_content",
    "modified_material",
    "cloud_unavailable",
    "unreadable_material",
)


RESERVED_FUTURE_SCENARIOS: tuple[dict[str, str], ...] = (
    {
        "name": "canvas_auth_expired",
        "target_phase": "Canvas connector",
        "why": "Users need a recoverable path when LMS authorization expires.",
        "status": "NOT IMPLEMENTED / RESERVED",
    },
    {
        "name": "canvas_rate_limited",
        "target_phase": "Canvas connector and retry policy",
        "why": "Remote API limits must not corrupt local study state.",
        "status": "NOT IMPLEMENTED / RESERVED",
    },
    {
        "name": "remote_material_added",
        "target_phase": "Remote sync",
        "why": "New LMS material should appear with provenance and user-visible change history.",
        "status": "NOT IMPLEMENTED / RESERVED",
    },
    {
        "name": "remote_material_updated",
        "target_phase": "Remote sync and MaterialVersion migration",
        "why": "Updated remote files require versioning without destroying user overlays.",
        "status": "NOT IMPLEMENTED / RESERVED",
    },
    {
        "name": "remote_material_deleted",
        "target_phase": "Remote sync",
        "why": "Remote removals need clear local retention and missing/removed semantics.",
        "status": "NOT IMPLEMENTED / RESERVED",
    },
    {
        "name": "deadline_changed",
        "target_phase": "AcademicEvent and AcademicAction",
        "why": "Deadline changes must update what matters today without overwriting personal targets.",
        "status": "NOT IMPLEMENTED / RESERVED",
    },
    {
        "name": "remote_local_conflict",
        "target_phase": "SyncPlan and Inbox",
        "why": "Conflicts between provider facts and user-owned overlays require judgment.",
        "status": "NOT IMPLEMENTED / RESERVED",
    },
    {
        "name": "offline_connector",
        "target_phase": "Connector lifecycle",
        "why": "StudyHub should preserve useful offline work when an LMS is unavailable.",
        "status": "NOT IMPLEMENTED / RESERVED",
    },
    {
        "name": "low_confidence_entity_match",
        "target_phase": "Academic Entity Resolution",
        "why": "Uncertain course/material matches should go to Inbox rather than being silently applied.",
        "status": "NOT IMPLEMENTED / RESERVED",
    },
    {
        "name": "stale_derived_artifact",
        "target_phase": "DerivedArtifact",
        "why": "Indexes, previews, and embeddings must be rebuildable from authoritative sources.",
        "status": "NOT IMPLEMENTED / RESERVED",
    },
    {
        "name": "source_anchor_stale",
        "target_phase": "SourceAnchor and Evidence",
        "why": "Notes and evidence need a recovery path when source structure changes.",
        "status": "NOT IMPLEMENTED / RESERVED",
    },
    {
        "name": "background_job_interrupted",
        "target_phase": "Background jobs",
        "why": "Interrupted extraction/sync jobs must resume or explain their state safely.",
        "status": "NOT IMPLEMENTED / RESERVED",
    },
)


def scenario_summary(scenario: SyntheticScenario) -> dict[str, Any]:
    files = sorted(path for path in scenario.library.rglob("*") if path.is_file())
    return {
        "name": scenario.name,
        "library": str(scenario.library),
        "file_count": len(files),
        "expectations": dict(scenario.expectations),
    }


def write_text(path: Path, text: str, *, mtime: int = FIXED_MTIME) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def write_bytes(path: Path, data: bytes, *, mtime: int = FIXED_MTIME) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


def _base(name: str, root: Path) -> tuple[Path, Path, Path, Path]:
    scenario_root = root / name
    library = scenario_root / "StudyLibrary"
    runtime = scenario_root / "runtime"
    database = runtime / "studyhub.sqlite"
    library.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    return scenario_root, library, runtime, database


def _copy_fixture_library(library: Path) -> None:
    shutil.copytree(FIXTURE_ROOT, library, dirs_exist_ok=True)
    for path in library.rglob("*"):
        if path.is_file():
            os.utime(path, (FIXED_MTIME, FIXED_MTIME))


def _large_library(library: Path) -> None:
    for course_index in range(1, 4):
        course = library / f"TEST9{course_index:03d} - Synthetic Scale Course {course_index}"
        for item_index in range(1, 101):
            week = (item_index - 1) % 10 + 1
            kind = "Lecture" if item_index % 2 else "Tutorial"
            section = "01 Course Materials" if kind == "Lecture" else "02 Exercises"
            phrase = "needle-scale-target" if course_index == 2 and item_index == 42 else f"scale-token-{course_index}-{item_index}"
            write_text(
                course / f"Week {week:02d}" / section / kind / f"Scale Material {item_index:03d}.txt",
                "\n".join(
                    [
                        f"TEST9{course_index:03d} synthetic scale material {item_index:03d}.",
                        f"Stable search phrase: {phrase}.",
                    ]
                ),
            )


def build_scenario(name: str, root: Path) -> SyntheticScenario:
    if name not in EXECUTABLE_SCENARIOS:
        raise ValueError(f"Unknown synthetic scenario: {name}")
    scenario_root, library, runtime, database = _base(name, root)
    expectations: dict[str, Any] = {}

    if name == "clean_empty":
        expectations.update({"courses": 0, "materials": 0})

    elif name == "normal_small":
        _copy_fixture_library(library)
        expectations.update(
            {
                "courses": 3,
                "materials": 10,
                "known_phrase": "planned aggregate expenditure",
                "known_course": "TEST1001",
                "known_week": "Week 04",
            }
        )

    elif name == "empty_course":
        expectations.update({"course_code": "TEST4100", "materials": 0})

    elif name == "long_metadata":
        write_text(
            scenario_root / "external" / ("Long Synthetic Reading " + "A" * 80 + ".txt"),
            "Long metadata synthetic content with safe bounded labels.",
        )
        expectations.update(
            {
                "course_code": "TEST-LONG-METADATA-COURSE-00000001",
                "course_name": "Synthetic Long Metadata Course " + "A" * 70,
                "week_label": "Synthetic Long Metadata Week " + "B" * 45,
                "known_phrase": "safe bounded labels",
            }
        )

    elif name == "large_library":
        _large_library(library)
        expectations.update({"courses": 3, "materials": 300, "known_phrase": "needle-scale-target"})

    elif name == "missing_original":
        write_text(
            library / "TEST4201 - Synthetic Missing Original" / "Week 01" / "01 Course Materials" / "Lecture" / "Keep.txt",
            "Synthetic keep file remains valid.",
        )
        write_text(
            library / "TEST4201 - Synthetic Missing Original" / "Week 01" / "01 Course Materials" / "Lecture" / "Remove.txt",
            "Synthetic file that will be removed after the first scan.",
        )
        expectations.update({"materials": 2, "missing_filename": "Remove.txt", "kept_filename": "Keep.txt"})

    elif name == "duplicate_content":
        content = "Duplicate synthetic content with checksum equality.\n"
        write_text(library / "Week 01" / "01 Course Materials" / "Lecture" / "Duplicate A.txt", content)
        write_text(library / "Week 01" / "01 Course Materials" / "Lecture" / "Duplicate B.txt", content)
        expectations.update({"detected": 2, "added": 1, "duplicates": 1})

    elif name == "modified_material":
        write_text(
            library / "TEST4301 - Synthetic Modified Material" / "Week 01" / "01 Course Materials" / "Lecture" / "Mutable.txt",
            "Initial synthetic version with alpha-content.",
            mtime=FIXED_MTIME,
        )
        expectations.update({"filename": "Mutable.txt", "initial_phrase": "alpha-content", "updated_phrase": "beta-content"})

    elif name == "cloud_unavailable":
        write_text(
            library / "TEST4401 - Synthetic Offline Workspace" / "Week 01" / "01 Course Materials" / "Lecture" / "Offline.txt",
            "Offline synthetic material remains searchable without cloud services.",
        )
        expectations.update({"known_phrase": "remains searchable without cloud"})

    elif name == "unreadable_material":
        write_bytes(
            library / "TEST4501 - Synthetic Degraded Content" / "Week 01" / "01 Course Materials" / "Lecture" / "Not Really A PDF.pdf",
            b"<html><body>Synthetic login page saved with a PDF extension.</body></html>\n",
        )
        expectations.update({"filename": "Not Really A PDF.pdf", "suspicious": "PDF extension but looks like HTML/login page"})

    return SyntheticScenario(name=name, root=scenario_root, library=library, database=database, runtime=runtime, expectations=expectations)
