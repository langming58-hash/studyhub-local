# Backup Export Format

Status: **AUTHORITATIVE `studyhub-backup-v1` EXPORT CONTRACT**.

StudyHub's implemented backup surface exports a privacy-safe, portable
projection of durable `USER_OWNED` state. It is not a database dump, an
academic-file archive, or a complete backup/restore system. Restore, relinking,
conflict resolution, scheduling, cloud backup, and encryption are not
implemented.

## Format Identity And Versioning

- Archive format: ZIP
- Manifest identity: `studyhub-backup`
- Current format version: `1`
- Conventional name: `studyhub-backup-v1`

The backup format version is independent of the application version and SQLite
migration number. Writers emit only the current version. The v1 validator
rejects unknown versions. A future reader may explicitly support older
versions, but removing or renaming required fields, changing required meaning,
or adding required members requires a format-version bump. Optional additions
must first define how older readers can safely ignore them; v1 itself uses an
exact member and field allowlist.

## Archive Layout

```text
manifest.json
data/terms.json
data/courses.json
data/learning_units.json
data/material_overlays.json
data/local_sources.json
data/notes.json
data/stars.json
data/study_records.json
data/ai_history.json
data/connector_user_state.json
data/whats_new_state.json
```

Every data member is UTF-8 canonical JSON with this envelope:

```json
{"dataset":"notes","format_version":1,"records":[]}
```

Objects use stable field names, records sort by portable stable identity, and
JSON keys are sorted without insignificant whitespace. SQLite integer IDs and
insertion order are not portable identity.

## Manifest

`manifest.json` records:

- `format`, `format_version`, and `minimum_restore_format_version`
- creation time and non-secret app/runtime profile labels
- feature declarations, including `restore_supported: false`
- the exact data-member inventory
- SHA-256, byte size, and record count for every data member
- total record count

It never records a username, machine ID, home directory, Canvas origin,
credential, signed URL, source absolute path, or runtime database path.

## Included User-Owned State

The v1 projection includes only allowlisted fields:

- terms and local/user-managed course presentation state
- managed learning units
- user material presentation/classification overlays
- safe descriptors for local user-owned sources
- notes, stars, bookmarks, attempts, wrong-question state, and study sessions
- field-allowlisted local AI conversation history
- What's New acknowledgements keyed by academic-event stable identity

Canvas compatibility courses are included only when they carry an explicit
user presentation overlay. Remote CourseOffering facts remain excluded.

## Local Source Portability

A user-owned source under the configured StudyLibrary may use a normalized,
non-absolute, traversal-safe library-relative path plus safe display metadata,
known SHA-256, and byte size. An external referenced original is represented as
`relink_required` with a safe basename and logical Material relationship. The
absolute source path is never exported. No relinking is attempted in v1.

## Explicit Exclusions

The archive contains none of the following:

- original local academic files, Canvas retained bytes, or Blob bytes
- extracted text, document chunks, questions/solutions, previews, thumbnails,
  indexes, embeddings, vector state, caches, or logs
- the runtime SQLite database or SQLite integer identities
- Canvas tokens, OpenAI keys, CredentialStore contents, authorization headers,
  handoff sessions, OAuth secrets, or any plaintext credential fallback
- Canvas origin, raw provider IDs/payloads, signed URLs, provider file/vector
  IDs, or sync baselines
- absolute local/runtime/cache paths

The full academic change journal is excluded because it represents committed
remote/derived facts. Only the separate user-owned seen acknowledgement is
exported. A future restore may reattach it only when the identical event stable
ID already exists; it must not manufacture an event.

Canvas CourseOffering selection is user intent but cannot yet be reconciled
without origin/raw remote identity. `connector_user_state.json` is therefore
empty in v1. This is an intentional omission, not a restore promise.

## AI History

AI export uses a field allowlist: title, safe scope labels, timestamps,
portable StudyHub references, and user/assistant message text. It does not dump
context, sources, questions, solutions, provider responses, request IDs, or
backend handles. Export text is bounded and redacts recognizable credentials,
signed/provider URLs, and absolute filesystem paths.

## Snapshot And Publication

Export reads all datasets from one SQLite read transaction and always rolls
that transaction back. It does not scan, rebuild indexes, acknowledge events,
contact Canvas/OpenAI/CredentialStore, or update user state.

The writer creates a temporary file inside the active runtime profile's
`exports/` directory, closes it, validates it, and atomically renames it to the
final ZIP. Validation failure removes the controlled temporary file and never
publishes a final archive. A process crash may leave a hidden temporary file;
such a file is not a valid published backup.

`POST /api/backup/export` accepts no destination path or dataset selector. It
uses the standard loopback, exact-origin, and CSRF boundary and returns only a
safe export ID, filename, size, timestamp, and format metadata. Demo/Test
exports remain inside isolated disposable profile state.

## Read-Only Validation

`validate_backup_archive(path)` does not extract files or mutate the database.
It rejects:

- missing, duplicate, or unexpected members
- absolute paths, traversal, backslash confusion, and ZIP symlinks
- excessive entry count, archive size, member size, expanded size, or records
- malformed UTF-8/JSON, unknown format versions, and non-allowlisted schemas
- checksum, byte-size, record-count, stable-ID, and checkable-reference errors
- non-empty v1 connector state

The validator reads bounded members directly from the ZIP. Successful
validation means only that the archive conforms to this export contract; it
does not restore or prove that external source files still exist.

## Forward Restore Contract

A later restore PR must remain separate and must implement Validate -> Preview
-> Explain -> Relink/Conflict Decisions -> Apply -> Undo or rollback recovery.
It must not infer ownership from table placement, overwrite originals, create
remote facts, or silently discard unresolved references. Until that focused
work exists, StudyHub supports versioned export and inspection only.
