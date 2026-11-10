# UCSB Schedule Optimizer UI Design System

Status: Phase 1 specification  
Direction: **Gaucho Route Builder / Coastal Registration Studio**

## 1. Product Experience

The optimizer is a focused academic planning workspace, not a marketing page or a generic dashboard. Its signature element is an **enrollment route**: selected courses appear as numbered registration tickets connected in the order they were added. The weekly calendar remains the dominant surface.

The interface has three visible stages:

1. **Choose courses** - search, inspect, add, reorder, or remove up to five courses.
2. **Set preferences** - choose preferred days, time window, minimum gap, and importance weights.
3. **Compare schedules** - generate conflict-free schedules, inspect scoring, and compare the top three results.

Desktop may show all stages together, but the active stage must be visually dominant. Mobile presents them sequentially without losing state.

## 2. Design Principles

- **Calendar first:** the schedule occupies most of the working viewport.
- **One visual metaphor:** course tickets, route lines, and numbered results create the identity.
- **UCSB without imitation:** use UCSB-inspired navy and gold with coastal supporting colors; do not reproduce GOLD's interface.
- **Explain the optimizer:** every score must be understandable in plain language.
- **Motion communicates change:** animate additions, optimization, and changed meetings only.
- **Dense but calm:** avoid nested cards, oversized headings, excessive borders, and permanent sidebars.

## 3. Tokens

Define these once in `styles/tokens.css`. Components must consume semantic variables rather than raw colors.

```css
:root {
  --color-navy-900: #003660;
  --color-navy-700: #07527c;
  --color-gold-500: #febc11;
  --color-gold-700: #b77f00;
  --color-pacific-600: #047c91;
  --color-sea-600: #09847a;
  --color-coral-500: #ef5645;
  --color-coral-700: #c43424;

  --color-canvas: #f7f9f8;
  --color-surface: #ffffff;
  --color-surface-muted: #eef3f4;
  --color-ink: #17242d;
  --color-text-muted: #586771;
  --color-line: #d7e1e4;
  --color-focus: #febc11;
  --color-success: #087f5b;
  --color-danger: #c43424;

  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 24px;
  --space-6: 32px;
  --space-7: 48px;

  --radius-control: 4px;
  --radius-panel: 6px;
  --shadow-popover: 0 12px 30px rgb(0 54 96 / 18%);
  --focus-ring: 0 0 0 3px var(--color-focus);

  --duration-fast: 140ms;
  --duration-standard: 240ms;
  --duration-emphasis: 420ms;
  --ease-standard: cubic-bezier(.2, .8, .2, 1);
}
```

### Course colors

Assign colors by selected-course position and keep the assignment stable until removal: Pacific teal, sea green, gold, coral, then navy-700. Lectures use the saturated color; sections/labs use a pale tint with a saturated left edge. Never use color alone to distinguish meeting type or selection.

### Typography

- Display and section headings: `Sora`, fallback `Manrope`, `Inter`, sans-serif.
- Interface and body text: `Inter`, fallback system sans-serif.
- Course codes, times, and enrollment codes: `IBM Plex Mono`, fallback monospace.
- Page title: 24px/30px, weight 700.
- Section title: 18px/24px, weight 700.
- Body: 14px/20px, weight 400 or 500.
- Labels/data: 12px/16px; uppercase only for short category labels.
- Do not scale font size with viewport width. Letter spacing is `0`.

## 4. Desktop Layout

The application fills the viewport using these full-width bands:

1. **Masthead, 56px:** product name left; term and dataset freshness right.
2. **Enrollment deck, auto height:** search control, horizontal selected-course route, preferences toggle, and Generate button.
3. **Preference tray, collapsed by default:** a shallow horizontal control band.
4. **Result summary, 64-88px:** result count, score, and key statistics.
5. **Calendar workspace:** remaining height; sticky weekday header and time rail.

The calendar should occupy at least 65% of the viewport height at 1440x900. The ranked-result selector attaches to the calendar's upper-right edge as `BEST`, `02`, and `03` tabs. Avoid permanent left/right control columns.

### Enrollment route

- Search is the first stop and remains visible.
- Each selected course is a compact ticket showing route number, course code, title, and lecture/section counts.
- Tickets use fixed responsive dimensions so content changes do not shift the route.
- Tickets support remove and keyboard reorder actions.
- The next empty stop contains the Add course action.
- Route overflow scrolls horizontally with visible edge affordances.

### Calendar

- White drafting-sheet surface with subtle blue-gray horizontal rules.
- Five weekday columns and a sticky time rail.
- Weekday headers use a narrow course-color activity marker, not a filled dark bar.
- Meeting blocks show course code, meeting type, and time; location appears when height permits.
- Selecting a meeting opens a persistent detail popover with course, type, days, time, location, instructor, enrollment code, and availability when supplied by the API.
- Blocks that change between ranked results animate; unchanged blocks remain still.

## 5. Responsive Behavior

### Tablet: 768-1099px

- Enrollment route becomes horizontally scrollable.
- Preference controls wrap into two rows.
- Calendar retains five columns with sticky headers and horizontal overflow only when needed.
- Result tabs become a horizontal segmented tab row above the calendar.

### Mobile: 320-767px

- Show one stage at a time with a persistent three-step indicator.
- Search opens as a full-width command surface.
- Selected tickets become a vertical compact list.
- Preferences open in a bottom sheet with backdrop, close button, focus trap, and Escape handling.
- Default schedule view is a chronological agenda grouped by day.
- Users may switch to a single-day calendar with horizontal day snapping.
- Never shrink the five-day desktop grid into the viewport.
- No page-level horizontal overflow at 320px.
- Keep the primary action reachable without covering content.

## 6. Interaction and Motion

Use animation only for meaningful state transitions:

- **Add course:** the ticket enters the route, then a short matching-color trace points toward the calendar (`240-420ms`).
- **Remove course:** ticket collapses and later stops close the gap (`240ms`).
- **Generate:** one gold scan crosses the calendar; result blocks then settle by weekday (`420ms` plus a maximum 40ms stagger).
- **Switch result:** only changed blocks fade/translate vertically (`240ms`).
- **Open preferences:** tray or bottom sheet reveals controls (`240ms`).

Hover motion is limited to primary actions and interactive tickets. Honor `prefers-reduced-motion: reduce` by removing translation, scan, pulse, and stagger effects while preserving instant state changes.

## 7. Required States

Every data-driven area implements:

- **Initial:** clear next action; no decorative fake data in the calendar.
- **Loading:** stable skeleton dimensions; preserve previous results during regeneration.
- **Empty search:** query-specific message and recovery action.
- **Search error:** identify the failed operation and offer Retry.
- **Selected limit:** explain the five-course limit without relying on a tooltip.
- **Generating:** disable duplicate submission and announce progress.
- **No valid schedules:** distinguish unavoidable course conflicts from strict preferences.
- **Results:** show raw combinations, valid combinations, rank, score, and freshness.
- **Stale/sample data:** persistent status badge with an accessible explanation.
- **API error:** preserve selections/preferences and allow retry.

## 8. Accessibility

- Meet WCAG 2.2 AA contrast for text, controls, focus, and course colors.
- Use native buttons, inputs, checkboxes/toggles, selects, and radio groups where possible.
- Search uses a combobox/listbox pattern; do not nest buttons inside `role="option"`.
- Ranked schedules use tabs with `aria-controls`, tab panels, and roving `tabindex`.
- Announce search counts, generation progress, errors, and new results through an `aria-live` region.
- Provide a visible 3px focus indicator on every interactive element.
- All calendar information must be available without hover.
- Tooltips use `aria-describedby` and never contain required-only information.
- Support keyboard-only use, 200% zoom, and touch targets of at least 44x44px.
- The mobile preference sheet traps focus, closes with Escape, and restores focus to its trigger.

## 9. Implementation Rules

- Build from one token system; do not migrate the reference HTML's layered CSS overrides.
- Keep optimization, conflict detection, and scoring in the Python backend.
- Render API strings through normal framework escaping; do not use raw HTML injection.
- Separate mock and real API clients explicitly with an environment flag.
- Use stable IDs from the API as React keys, never array positions.
- Prefer CSS transitions driven by application state; avoid forced reflow animation tricks.

## 10. Phase 2 Acceptance Criteria

- The app clearly communicates the three-stage workflow within five seconds.
- A user can search, add two courses, edit preferences, generate, and switch among three results using only the keyboard.
- The calendar is the largest visual surface at desktop sizes.
- The interface uses the route/ticket concept consistently and does not resemble a generic card dashboard.
- Score details explain days, time range, and gaps in plain language.
- Desktop works at 1280x720 and 1440x900 without overlapping controls.
- Mobile works at 320x568 and 390x844 without page-level horizontal overflow.
- Loading, empty, error, no-results, sample-data, and successful-result states are represented.
- Reduced-motion mode removes nonessential animation.
- Automated checks find no critical accessibility violations; manual keyboard navigation completes the primary workflow.

