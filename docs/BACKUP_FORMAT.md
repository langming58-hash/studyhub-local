# Backup Export Format

Status: **AUTHORITATIVE `studyhub-backup-v1` EXPORT CONTRACT**.

StudyHub's implemented backup surface exports a privacy-safe, portable
projection of durable `USER_OWNED` state. It is not a database dump or an
academic-file archive. StudyHub can now
select and privately stage an archive, validate it, produce a deterministic
zero-mutation restore preview, explicitly verify a user-selected exact relink
candidate, transactionally apply the supported safe actions, discover the
latest completed Restore, and explicitly Undo a still-safe Restore. Automatic
relinking, Redo, scheduling, cloud backup, and encryption are not implemented.

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
selected original, and transactional Apply can attach that exact user-owned
file by reference without copying or mutating it.

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
decisions are explicit. Eligible user-owned conflicts can either keep current
state or restore the validated backup value; local-source/content conflicts can
only keep current because backup v1 contains no original bytes. Unresolved and
unsupported items can only be skipped. For a
relink-required local source, the desktop app can explicitly choose one
replacement original and verify exact SHA-256 and byte-size identity without
changing restore state. An exact current capability enables an explicit
`RELINK` decision; otherwise relink-required records remain skippable only.
Discard removes only private staged state.

## Explicit Source Relink Preview

The Tauri desktop boundary accepts only the opaque restore ID and backup local-
source stable ID before opening a native single-file picker. It stores the
selected absolute path in a profile-private, permission-restricted EPHEMERAL
descriptor under `restore-relink-inbox/`; browser JavaScript receives only an
opaque relink ID and safe display metadata. Demo/Test cannot select external
files.

`POST /api/backup/restore-relink-preview` accepts only the restore ID, expected
base RestorePlan revision, source stable ID, opaque relink ID, and any already
approved opaque relink capabilities. It validates one
immutable archive snapshot, recomputes the canonical plan query-only, verifies
every capability's ownership and private-path containment, and streams each
selected regular file through SHA-256. Only exact size and hash equality returns
`RELINK_READY`; every content mismatch returns `RELINK_MISMATCH`. The derived
per-source revision binds the immutable archive/local-state snapshot and that
source's expected/observed content identity, not the transient accumulated
selection set, absolute path, random capability ID, or time. Adding another
exact source therefore preserves earlier ready candidates while the returned
effective plan reflects the full cumulative selection.

Relink preview does not copy, move, rename, chmod, write, or delete the selected
original and performs no database, index, network, Canvas, OpenAI, or credential
operation. Discard removes only the private descriptor. Discarding its staged
restore invalidates associated descriptors. Otherwise a descriptor remains
private EPHEMERAL state until explicitly discarded or successfully applied;
there is no background cleanup daemon or cross-profile fallback.

## Transactional Restore Apply

`POST /api/backup/restore-apply` accepts only an opaque staged restore ID, an
exact expected plan revision, decisions for actions in the recomputed plan,
and optional opaque relink IDs plus their preview revisions. It never accepts a
path, hash, byte size, or caller-supplied file identity.
Inside one process-local restore lock it acquires and validates one immutable
archive snapshot, opens only the existing current-profile database, starts
`BEGIN IMMEDIATE`, and recomputes the canonical plan. A changed archive, relevant
target state, or inspected source candidate fails closed and rolls back.

Safe `CREATE`, `UPDATE_USER_STATE`, and explicitly selected `RESTORE_BACKUP`
actions restore allowlisted v1 terms,
local courses, managed learning units, material overlays on resolved Materials,
notes, stars, attempts, wrong-question state, study sessions, bookmarks, and
acknowledgement of an existing identical academic-change event. Multiset records
restore exact missing multiplicity, and private deterministic conflict targeting
updates only the recomputed matching row. No SQLite row ID enters the backup,
plan, revision, or browser request. `MATCH`, `NOOP`, `KEEP_LOCAL`, and `SKIP`
perform no write. The lower apply helper never commits or rolls back; the caller
owns the single transaction, so any failure rolls back every restore mutation.

For an explicit `RELINK`, Apply reloads the profile-private capability, verifies
its restore/source binding, re-hashes the regular file, recomputes the immutable
base plan and cumulative effective plan under `BEGIN IMMEDIATE`, rechecks each
independent relink revision, and verifies every file again
immediately before commit. It updates or creates the one canonical user-owned
Source/Material projection and derived search index by reference. It never
copies, moves, deletes, or rewrites the original. Successful Apply consumes the
private descriptor; failed Apply preserves a still-valid descriptor and rolls
back database/index changes. Apply does not restore Canvas or AI state or
contact a network/credential service. `RESTORE_BACKUP` never writes source
bytes, Source/Blob/MaterialVersion identity, or remote-authoritative Canvas
facts.

When a successful Apply makes reversible mutations, it also writes a private
versioned Restore operation and ordered ChangeSet inside that same transaction.
The operation has a server-generated opaque public-safe identity, immutable
digest, schema fingerprint, safe counts, and `APPLIED` status. Change rows use a
closed entity/change-kind allowlist and exact private before/post images of the
closed reversible field contract. Capture is targeted from the validated plan,
decisions, and relinks; it never scans unrelated durable rows merely to diff the
database. The fingerprint binds the ChangeSet format, reversible fields,
locators, change kinds, and artifact contract rather than unrelated schema
migrations. These rows are runtime-local recovery data, not portable backup
content.

The ChangeSet excludes rebuildable FTS, chunks, parsed questions/solutions,
course/week counts, and extracted cache contents. Relink-created cache state is
represented only by a controlled relative cache identity and only when Restore
created the artifact. A Restore containing only `MATCH`, `NOOP`, `KEEP_LOCAL`,
or `SKIP` writes no operation. The latest-operation endpoint exposes only a safe
aggregate summary. Read-only Undo Preview validates postimages, dependencies,
and later Restore overlap; explicit Undo Apply repeats those gates under the
Restore lock and `BEGIN IMMEDIATE`, then reverses the exact ChangeSet atomically.
The private journal remains bounded with capacity derived
from backup v1's maximum uncompressed size, record count, dual pre/post images,
and serialization overhead; an incomplete ChangeSet always aborts Restore
atomically rather than truncating recovery data.

Redo, generic action-history rollback, automatic backup scheduling, cloud
backup, backup encryption, and source-byte backup remain out of scope.

When Apply restores a material overlay, the same transaction synchronizes the
existing legacy compatibility row, its existing Phase-1 Material projection,
and local derived search metadata. Section is derived from the restored
material type, while exported category and exercise-type values remain the
user-owned overlay. This narrow synchronization never creates or changes a
Source, Blob, MaterialVersion, current version, byte hash, or extracted content.
