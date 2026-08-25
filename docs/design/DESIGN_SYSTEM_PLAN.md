# Design System Plan

Principles:

- Content over chrome.
- Learning over system status.
- Progressive disclosure over permanent controls.
- Calm neutral surfaces with restrained accent use.

Reusable patterns:

- App shell: fixed/collapsible sidebar plus contextual topbar.
- Page header: title, quiet subtitle, at most two contextual actions.
- List rows: filename/course/week/category first; secondary actions remain quiet.
- Empty state: explain what is empty and the next useful action.
- Settings panels: system details live here, not on Home.
- AI workspace: one conversation/history/source system shared across global and contextual entry points.

Accessibility choices:

- Prefer native buttons, inputs, select, textarea, and dialog.
- Keep visible focus indicators.
- Use `aria-current` for active navigation and `aria-label` for icon-only controls.
- Preserve keyboard paths for hover-revealed actions.

Responsive strategy:

- Wide/desktop: sidebar plus full content width, document/AI split where relevant.
- Normal laptop: narrower sidebar, row-based lists, restrained panels.
- Narrow/tablet: collapsed navigation and single-column page content; document preview remains primary.
