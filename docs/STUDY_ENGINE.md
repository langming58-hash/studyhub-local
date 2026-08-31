# Study Engine

The Study Engine turns indexed material into a small, local study loop. It does
not change, annotate, or replace the original course files.

## Material State

`material_study_state` stores one optional row per indexed file:

- `not_started`: the default when no row exists
- `in_progress`: set when a material is first opened or reopened
- `completed`: set explicitly by the user
- `needs_review`: an independent review flag that can apply to any status

The row also records local timestamps for first start, completion, last open,
and last review. Removing or resetting this metadata leaves the original file
untouched.

## Progress

Course, week, and library progress are calculated from active, present files in
non-archived courses. A material contributes to completion only when its state
is `completed`. Missing files, removed entries, archived courses, and stale
empty courses do not inflate the totals.

## Study Queue

The queue is deterministic and bounded. It prioritizes:

1. material marked `needs_review`
2. material already `in_progress`
3. material not yet started

Within a priority, recently opened or updated material appears first. Completed
material stays out of the queue unless it is explicitly marked for review.

## Search Relationship

Search and the Study Engine share file identifiers and academic metadata but
remain separate concerns. Search ranks filename and readable-content matches,
then applies small boosts for the current course and week. Opening a result can
advance its study state without changing search content or source provenance.

## Privacy Boundary

Study state, notes, stars, attempts, wrong-question records, and AI conversation
history live in the local runtime database. Public API responses expose only
allowlisted academic metadata and status fields. They do not expose absolute
paths, cache paths, credentials, provider IDs, or extracted-text storage paths.

Synthetic fixtures and populated documentation screenshots are test-only. The
production workspace remains empty on first launch.
