# Design And Interaction Constitution

Status: **AUTHORITATIVE TARGET DESIGN DIRECTION**.

This document governs future product design. It does not request a redesign of
current screens. The implemented navigation is recorded in
[Current IA](CURRENT_IA.md).

## Precision Academic Desktop

StudyHub is a dense academic desktop workspace, not a marketing site or mobile
dashboard enlarged for desktop.

Core qualities:

- high information density
- low visual noise
- content before decoration
- hierarchy before cards
- desktop-native behavior where native behavior is better
- keyboard-first interaction
- progressive disclosure
- calm, precise, structured, professional, restrained presentation

## Visual Language

Prefer typography, alignment, spacing, thin borders, subtle surfaces, and a
limited semantic color system. Color communicates state, ownership, warning,
selection, or provenance rather than decoration.

Avoid:

- giant rounded cards or page sections presented as cards
- decorative gradients, glow, and excessive glassmorphism
- rainbow course cards and one-color theme saturation
- pill-heavy interfaces and permanent button walls
- oversized mobile-style controls
- emoji interface icons
- gamification, confetti, and decorative motion

Donor UI components must lose donor product skin and use StudyHub tokens,
spacing, typography, icons, and interaction behavior.

## Information Architecture

The TARGET information architecture is distinct from the navigation currently
shipped and does not claim implementation. Current behavior remains documented
in [Current IA](CURRENT_IA.md).

- Home provides environmental awareness: what changed, what can be continued,
  and an overview across courses.
- Today is an executable queue of `AcademicAction` items.
- Library contains all academic materials across sources and courses.
- Course is the contextual workspace for one course or course offering.
- Search provides global information retrieval.
- Review supports long-term memory and review work.
- Inbox resolves conflicts, uncertain matches, sync issues, and other items
  requiring user judgment.
- Settings owns capabilities, configuration, privacy, and diagnostics.

A Course workspace may contain Overview, Materials, Assessments, Notes, Review,
and Timeline views.

Ask/AI is a contextual, source-grounded knowledge capability. It may preserve
conversation history and dedicated working surfaces, but the target
architecture does not require AI to occupy a permanent primary-navigation
destination.

Command, Search, and Ask are separate semantic concepts even when they share an
input, menu, or overlay primitive.

## Interaction Language

Target shortcuts:

| Shortcut | Action |
| --- | --- |
| `Cmd+K` | Open Commands |
| `Cmd+Shift+F` | Open Global Search |
| `Cmd+,` | Open Settings |
| `Space` | Quick Look selected content |
| `Esc` | Close the active contextual layer |

Secondary actions normally live in context menus, inspectors, selection state,
hover affordances, or the command palette. Keep only frequent, high-confidence
commands permanently visible.

Potentially destructive or ambiguous changes should follow:

```text
Preview -> Explain -> Apply -> Undo
```

Prefer an affected-items summary and recovery path over a generic "Are you
sure?" prompt.

## Reusable Patterns

- App shell: stable sidebar and contextual top bar.
- Page header: title, quiet context, and no more than two prominent actions.
- Lists: academic identity first; secondary actions stay quiet.
- Inspector: selection-specific metadata, provenance, and less frequent work.
- Empty state: explain what is empty and offer the next useful action.
- Settings: capability details and diagnostics stay out of normal study flow.
- AI workspace: one conversation/history/source model across global and
  contextual entry points.
- Viewer: content remains primary, with source anchors and tools subordinate.

## Accessibility And Desktop Behavior

- Prefer native buttons, inputs, selects, textareas, and dialogs.
- Keep visible keyboard focus and a complete keyboard path.
- Use familiar icons and accessible names for icon-only controls.
- Preserve keyboard access to hover-revealed actions.
- Respect reduced motion and do not use motion as the only state signal.
- Use stable dimensions so loading, selection, labels, and controls do not
  produce layout shifts.
- Complete human VoiceOver validation before claiming full screen-reader
  support.

## Responsive Strategy

- Wide desktop: sidebar plus full workspace; document/AI splits where useful.
- Normal laptop: narrower navigation and compact row-oriented information.
- Narrow/tablet: collapsed navigation and single-column content while keeping
  the primary document or task dominant.

Responsive changes must preserve hierarchy and usable control sizes without
turning the desktop product into stacked card sections.

## Design Change Governance

Design changes require observed user friction, an explicit goal, screenshots at
relevant widths, keyboard/accessibility checks, and visual regression review.
Do not combine a broad redesign with schema migration, donor adoption, or sync
implementation.

The [Engineering Constitution](../ENGINEERING_CONSTITUTION.md) governs donor
selection and the distinction between CURRENT and TARGET behavior.
