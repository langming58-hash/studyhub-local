# Migration Plan

Data:

- Do not wipe or migrate existing files, stars, notes, wrong questions, AI conversations, or index tables.
- Keep original academic files as the authoritative source.
- Store only small UI preferences in `localStorage`: sidebar state, last opened file, AI split width, draft, and last conversation id.

Implementation phases:

1. Simplify global navigation and remove the persistent course list from the sidebar.
2. Redesign Home around continuation, latest material, and recent files.
3. Make Courses the canonical library surface, with scan/upload controls there.
4. Consolidate Practice, Wrong Questions, and Exam Review into Study.
5. Simplify Search around a single primary search field and progressive filters.
6. Keep the AI workspace as the single conversation/history system.
7. Move system metrics and diagnostics into Settings.
8. Validate synthetic public flows before porting equivalent UI to the private configured instance.

Boundaries:

- Public repo uses synthetic fixtures only.
- Private instance may be used for read-only validation against real files.
- No release is created until the user visually accepts the redesign.
