# Synthetic Scenario Harness

Status: **CURRENT TEST INFRASTRUCTURE / TARGET-SAFE MATRIX**.

StudyHub uses synthetic scenarios to prove behavior against realistic local
academic-library states without using private academic data. The executable
harness lives in:

```text
bin/synthetic_scenarios.py
bin/synthetic_scenario_acceptance.py
```

The harness creates mutable data only under temporary directories, uses
synthetic `TEST` course names and content, makes no network calls, and does
not read the user's home directory, real StudyLibrary, Application Support
state, credentials, logs, or private academic files.

It is intentionally small. It does not replace focused acceptance scripts such
as privacy, security, Ask AI, preview, first-run, desktop, release, or
environment-isolation checks.

## Executable Current Scenarios

These scenarios use behavior that StudyHub currently represents on `main`.

| Scenario | Behavior proven |
| --- | --- |
| `clean_empty` | An empty workspace has zero user courses and materials, first-run remains valid, and demo fixtures are not auto-seeded. |
| `normal_small` | Existing synthetic fixtures scan correctly, known source text is searchable, and a repeated unchanged scan is stable. |
| `empty_course` | A managed course with no materials remains representable and does not affect unrelated courses/materials. |
| `long_metadata` | Long but valid synthetic course, week/module, and material labels survive current validation without path leakage or truncation. |
| `large_library` | About 300 tiny generated materials scan deterministically, do not multiply on repeated scans, and remain locally searchable. |
| `missing_original` | Removing an original file marks the material missing with current semantics while preserving metadata/history and unrelated materials. |
| `duplicate_content` | Current duplicate-detection behavior is observable for same-checksum imports and originals are not overwritten. |
| `modified_material` | Changing a file preserves current material identity, records current version behavior, and makes updated text searchable. |
| `cloud_unavailable` | Local browsing/scanning/search remains useful with no OpenAI key, vector-store identifier, or network dependency. |
| `unreadable_material` | Unsupported or malformed content degrades without crashing or mutating the original file. |

Large-library data is generated at runtime and is never committed.

## Reserved Future Scenarios

These names are reserved for target capabilities that are not implemented in
the current product. They must remain documentation-only until the relevant
feature exists with code and tests.

| Scenario | Target capability/phase | Why it matters | Current status |
| --- | --- | --- | --- |
| `canvas_auth_expired` | Canvas connector | Users need a recoverable path when LMS authorization expires. | NOT IMPLEMENTED / RESERVED |
| `canvas_rate_limited` | Canvas connector and retry policy | Remote API limits must not corrupt local study state. | NOT IMPLEMENTED / RESERVED |
| `remote_material_added` | Remote sync | New LMS material should appear with provenance and user-visible change history. | NOT IMPLEMENTED / RESERVED |
| `remote_material_updated` | Remote sync and MaterialVersion migration | Updated remote files require versioning without destroying user overlays. | NOT IMPLEMENTED / RESERVED |
| `remote_material_deleted` | Remote sync | Remote removals need clear local retention and missing/removed semantics. | NOT IMPLEMENTED / RESERVED |
| `deadline_changed` | AcademicEvent and AcademicAction | Deadline changes must update what matters today without overwriting personal targets. | NOT IMPLEMENTED / RESERVED |
| `remote_local_conflict` | SyncPlan and Inbox | Conflicts between provider facts and user-owned overlays require judgment. | NOT IMPLEMENTED / RESERVED |
| `offline_connector` | Connector lifecycle | StudyHub should preserve useful offline work when an LMS is unavailable. | NOT IMPLEMENTED / RESERVED |
| `low_confidence_entity_match` | Academic Entity Resolution | Uncertain course/material matches should go to Inbox rather than being silently applied. | NOT IMPLEMENTED / RESERVED |
| `stale_derived_artifact` | DerivedArtifact | Indexes, previews, and embeddings must be rebuildable from authoritative sources. | NOT IMPLEMENTED / RESERVED |
| `source_anchor_stale` | SourceAnchor and Evidence | Notes and evidence need a recovery path when source structure changes. | NOT IMPLEMENTED / RESERVED |
| `background_job_interrupted` | Background jobs | Interrupted extraction/sync jobs must resume or explain their state safely. | NOT IMPLEMENTED / RESERVED |

Do not add executable assertions for these reserved scenarios by creating fake
target tables, fake Canvas APIs, fake sync conflicts, or fake background-job
models.
