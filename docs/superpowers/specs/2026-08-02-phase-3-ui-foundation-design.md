# Phase 3 UI Foundation Design

**Date:** 2026-08-02
**Status:** Approved for implementation planning

## Goal

Make Halo's existing workspace shell resilient enough for Phase 3 without
building any Phase 3 feature surface early. Preserve all current behavior while
fixing verified reflow, control-state, labeling, and accessibility defects with
the smallest practical patch.

## Evidence

The audit covered every production UI source file, the nine `ui_ux/`
specifications, Tauri window geometry, Phase 3 IPC projections, and the rendered
workspace at its normal, minimum, and 200%-equivalent widths.

- The current UI baseline is green: 68 Vitest tests pass and the rendered app
  emits no console warnings or errors.
- At the real 720x480 minimum, Settings overflows horizontally because the
  full-width roots textarea uses content-box sizing.
- At a 360px CSS viewport, equivalent to 200% zoom on the 720px minimum,
  Chat, Tasks, Activity, and Settings lose usable horizontal space. The fixed
  176px sidebar leaves only 181px for the active view.
- A failed task still exposes Stop and lane controls; completed tasks still
  expose lane controls.
- Activity's task filter exposes raw task UUIDs instead of task titles.
- Settings appears immediately below the primary navigation instead of at the
  bottom of the sidebar as specified.
- Several text-like controls are smaller than Halo's 32px interaction target,
  view group headings skip from `h1` to `h3`, and the main content lacks a
  `main` landmark.

## Chosen approach

Use a single CSS compact breakpoint at `640px` and make small corrections in
the existing components. This keeps normal 720px-and-wider desktop layouts
unchanged while allowing browser zoom and text scaling to trigger a compact
layout naturally.

Rejected alternatives:

- A stateful collapsible sidebar adds UI state, persistence, controls, and test
  paths that the responsive layout does not need.
- Raising the minimum window size or accepting horizontal scrolling avoids the
  layout work but fails the 200% text-resize and reflow requirement.

## Layout changes

At widths up to 640px:

- The sidebar becomes a 56px icon rail. Each button retains an explicit
  accessible name; visual labels are hidden without removing navigation
  semantics.
- The status strip may wrap. A running task's title and Stop control remain
  visible instead of forcing the workspace wider.
- Settings rows and action groups wrap within the available width. Inputs use
  border-box sizing and may shrink instead of setting an intrinsic width floor.
- Activity filters stay within the view and wrap by control rather than
  creating page-level horizontal scrolling.
- A disconnected-chat queue notice moves to its own wrapped row rather than
  squeezing the composer into an unusable column.
- Existing cards and approval actions wrap only when required. There is no new
  mobile navigation mode, drawer, or breakpoint state.

At every width, the Settings separator consumes the sidebar's remaining space
so Settings stays bottom-aligned.

## Component corrections

### Workspace and navigation

- Use a `main` landmark for the workspace content.
- Use `h2` for view groups beneath each view's existing `h1`.
- Give sidebar buttons explicit accessible names before hiding their visual
  labels in compact mode.

### Tasks

- Treat `done` and `failed` as terminal states.
- Terminal cards do not expose Stop or lane-changing controls.
- Keep their state, title, progress/reason, and historical visibility intact.

### Activity

- Read task titles from the existing task store and use them as filter option
  labels, falling back to the task id only when no title is known.
- Do not change activity ordering, virtualization, filtering, or undo behavior.

### Interaction targets

Bring the known undersized text controls in Chat, Activity, Memory, Skills,
Settings, and Approval surfaces up to the existing `--hit-min` token. Reuse
native buttons and current classes; do not introduce another button primitive.

## Data flow and behavior

There are no IPC, reducer, persistence, Brain, Voice, or Tauri command changes.
Activity gains one existing store selector (`tasks`) solely for display labels.
Responsive behavior is CSS-driven and therefore has no runtime state or
cross-window synchronization path.

All pending/confirmation behavior remains backend-confirmed. The task change
only removes invalid controls after a terminal state is already authoritative;
it does not optimistically change task state.

## Error handling

Existing inline errors, live regions, retry behavior, and reconnect behavior are
unchanged. Long or missing labels continue to truncate or wrap within their
current surfaces. Unknown activity task ids remain visible through the id
fallback rather than becoming blank options.

## Verification

Add focused regression coverage for:

- terminal tasks not exposing Stop or enabled lane controls;
- Activity task filters preferring titles with an id fallback;
- compact-navigation accessible names and corrected heading semantics where a
  component test is proportionate.

Rendered validation must cover:

- 1000x700 default and 720x480 minimum layouts;
- a 360px 200%-equivalent viewport with no page-level horizontal overflow;
- Chat, Tasks, Activity, Memory, Skills, Settings, and an approval card;
- light and dark themes;
- `Ctrl+K`, visible focus, and console warnings/errors.

Finish with TypeScript, Vitest, the production UI build, Ponytail diff review,
and the repository's full `verify.ps1` gate.

## Out of scope

- Coding-agent diff summaries and agent-specific task cards.
- Browser-run or playbook-specific UI.
- Lane 2 takeover banners and Lane 3 stream-subscription work.
- Real voice controls and self-improvement feature implementation.
- First-run onboarding, orb deep-link navigation, and a visible collapse button.
- A new component library, CSS framework, dependency, or design-token layer.

Those surfaces belong to their Phase 3 mini-phases and must extend the proven
task, activity, approval, and voice projections instead of being mocked here.
