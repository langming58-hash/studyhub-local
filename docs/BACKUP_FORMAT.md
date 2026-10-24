# Backup Export Format

Status: **AUTHORITATIVE `studyhub-backup-v1` EXPORT CONTRACT**.

StudyHub's implemented backup surface exports a privacy-safe, portable
projection of durable `USER_OWNED` state. It is not a database dump, an
academic-file archive, or a complete backup/restore system. StudyHub can now
select and privately stage an archive, validate it, produce a deterministic
zero-mutation restore preview, explicitly verify a user-selected exact relink
candidate, and transactionally apply the supported safe actions. Relink Apply,
backup-wins replacement, automatic relinking, post-commit undo,
scheduling, cloud backup, and encryption are not implemented.

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

Objects use stable field names, records sort by portable identity, and JSON
keys are sorted without insignificant whitespace. SQLite integer IDs and
insertion order are not portable identity. Append-only/event-like rows that do
not have a native stable ID use a canonical semantic identity plus a
deterministic occurrence index. This preserves duplicate records without
exposing row IDs and produces the same identity set for equivalent multisets.

Identity rules by v1 dataset are deliberately narrow:

- terms, courses, learning units, materials, and academic-change events retain
  their existing domain stable IDs or references derived from them
- stars and bookmarks use the uniquely targeted material plus record kind
- notes and attempts use their immutable semantic fields plus occurrence index
- wrong-question state uses question/material/course/creation identity plus
  occurrence index; mutable mastery, review count, and last-reviewed state are
  payload, not identity
- study sessions use course/start identity plus occurrence index; mutable or
  presentation state remains payload
- local source descriptors key from the associated stable Material identity
- AI history and Canvas connector selection have no claimed v1 identity and
  remain empty

For exactly identical occurrences, the current schema cannot distinguish which
historical row is which. V1 preserves the count and deterministic identity set,
not a fictional mapping back to SQLite row identity.

## Manifest

`manifest.json` records:

- `format`, `format_version`, and `minimum_restore_format_version`
- creation time and non-secret app/runtime profile labels
- feature declarations, including the boolean `restore_supported` capability
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
- What's New acknowledgements keyed by academic-event stable identity

Canvas compatibility courses are included only when they carry an explicit
user presentation overlay. Remote CourseOffering facts remain excluded.

## Local Source Portability

A user-owned source under the configured StudyLibrary may use a normalized,
non-absolute, traversal-safe library-relative path plus safe display metadata,
known SHA-256, and byte size. An external referenced original is represented as
`relink_required` with a safe basename and logical Material relationship. The
absolute source path is never exported. Relink preview can verify an explicitly
selected original, but no relink mutation is attempted in v1.

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

`data/ai_history.json` is a required but empty v1 member, and the manifest sets
`ai_history_included: false`. Current conversation/message rows do not have an
immutable portable identity: conversation titles and scopes can change, while
integer row IDs are runtime-local. StudyHub therefore does not claim false
restore identity or export AI history in v1. Renaming a conversation cannot
change backup content. A future focused migration may introduce durable stable
identities before AI history becomes portable.

## Snapshot And Publication

The HTTP and CLI entry points open the existing database with SQLite
`mode=ro`, enable `PRAGMA query_only=ON`, validate the required schema without
initializing or migrating it, and then read all datasets from one SQLite read
transaction that is always rolled back. Missing or incompatible databases fail
cleanly. Export does not scan, rebuild indexes, acknowledge events, contact
Canvas/OpenAI/CredentialStore, or update user state.

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
Both `data/ai_history.json` and `data/connector_user_state.json` must remain
empty in v1; non-empty records are rejected even if their JSON shape otherwise
resembles an older draft.

Incoming archives are untrusted input even when every manifest hash has been
recomputed. The canonical validator used by both Preview and Apply therefore
validates exact JSON types, boolean/integer separation, bounded text and
timestamps, control characters, closed v1 enums, SHA-256 syntax, numeric
ranges, and bounded safe source-relative paths before planning begins. It does
not coerce strings, integers, or booleans and does not sanitize malformed input.

## Read-Only Restore Preview

The desktop app selects one archive through a native picker and copies it into
the active profile's private `restore-inbox` using a generated opaque ID. The
localhost API accepts only that ID, never an arbitrary filesystem path. Failed
staging removes controlled temporary files and never modifies or deletes the
user-selected original.

Preview performs one bounded read of the staged regular file, then validates
and parses that same immutable in-memory archive snapshot before
reconciliation. It opens the current database through the dedicated
read-only/query-only backup connection, and
does not call initialization, migration, scanning, Canvas, OpenAI, or the
CredentialStore. It builds an ephemeral versioned `RestorePlan` with explicit
`CREATE`, `MATCH`, `UPDATE_USER_STATE`, `NOOP`, `UNRESOLVED_REFERENCE`,
`CONFLICT`, `RELINK_REQUIRED`, and `UNSUPPORTED_BY_V1` classifications. The
plan revision binds the validated member hashes, plan version, public actions,
explicit source inspection, and private hashes of the relevant current semantic
target state. Those private state hashes bind compared values and multiset
multiplicity without exposing local values through the API. SQLite row IDs,
absolute paths, runtime roots, restore-session identity, and timestamps ignored
by reconciliation remain excluded.

Terms, courses, and managed learning units reconcile by stable ID only. Local
source descriptors inspect only their explicit StudyLibrary-relative candidate;
there is no whole-library scan or basename guessing. Missing or externally
referenced originals require later explicit relinking. Material references are
never satisfied with fabricated placeholder Materials. Notes, attempts, and
study sessions preserve multiplicity. Differing wrong-question progress is a
conflict, and What's New acknowledgement state can reattach only to an existing
identical academic event. Remote facts, AI history, and Canvas connection state
remain excluded.

The Settings UI presents the complete grouped preview and omissions. Required
decisions are explicit: conflicts can only keep current state, while unresolved,
relink-required, and unsupported items can only be skipped for now. For a
relink-required local source, the desktop app can explicitly choose one
replacement original and verify exact SHA-256 and byte-size identity without
changing restore state. Discard removes only private staged state.

## Explicit Source Relink Preview

The Tauri desktop boundary accepts only the opaque restore ID and backup local-
source stable ID before opening a native single-file picker. It stores the
selected absolute path in a profile-private, permission-restricted EPHEMERAL
descriptor under `restore-relink-inbox/`; browser JavaScript receives only an
opaque relink ID and safe display metadata. Demo/Test cannot select external
files.

`POST /api/backup/restore-relink-preview` accepts only the restore ID, expected
RestorePlan revision, source stable ID, and opaque relink ID. It validates one
immutable archive snapshot, recomputes the canonical plan query-only, verifies
capability ownership and private-path containment, and streams the selected
regular file through SHA-256. Only exact size and hash equality returns
`RELINK_READY`; every content mismatch returns `RELINK_MISMATCH`. The derived
revision binds the current plan and expected/observed content identity, not the
absolute path, random capability ID, or time.

Relink preview does not copy, move, rename, chmod, write, or delete the selected
original and performs no database, index, network, Canvas, OpenAI, or credential
operation. Discard removes only the private descriptor. Discarding its staged
restore invalidates associated descriptors. Otherwise a descriptor remains
private EPHEMERAL state until explicitly discarded; there is no background
cleanup daemon or cross-profile fallback. A future mutating relink operation
must re-hash immediately before Apply; a prior `RELINK_READY` is not permanent
authorization.

## Transactional Restore Apply

`POST /api/backup/restore-apply` accepts only an opaque staged restore ID, an
exact expected plan revision, and decisions for actions in the recomputed plan.
Inside one process-local restore lock it acquires and validates one immutable
archive snapshot, opens only the existing current-profile database, starts
`BEGIN IMMEDIATE`, and recomputes the canonical plan. A changed archive, relevant
target state, or inspected source candidate fails closed and rolls back.

Safe `CREATE` and `UPDATE_USER_STATE` actions restore allowlisted v1 terms,
local courses, managed learning units, material overlays on resolved Materials,
notes, stars, attempts, wrong-question state, study sessions, bookmarks, and
acknowledgement of an existing identical academic-change event. Multiset records
restore exact missing multiplicity. `MATCH`, `NOOP`, `KEEP_LOCAL`, and `SKIP`
perform no write. The lower apply helper never commits or rolls back; the caller
owns the single transaction, so any failure rolls back every restore mutation.

Apply never copies or changes original academic files, creates a Material for a
missing source, restores Canvas or AI state, contacts a network/credential
service, or accepts browser-supplied paths. The private staged copy remains until
the user explicitly discards it. A successful commit currently has no automatic
Undo; source relink Apply and audited backup-wins decisions require later
focused work. Existing Apply continues to require `SKIP` for every
`RELINK_REQUIRED` action, even when an exact candidate has been previewed.

When Apply restores a material overlay, the same transaction synchronizes the
existing legacy compatibility row, its existing Phase-1 Material projection,
and local derived search metadata. Section is derived from the restored
material type, while exported category and exercise-type values remain the
user-owned overlay. This narrow synchronization never creates or changes a
Source, Blob, MaterialVersion, current version, byte hash, or extracted content.
