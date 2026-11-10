# Phase 3: Scheduler Engine

Phase 3 turns the normalized Winter 2027 catalog into course choices that the
optimizer can validate, score, and rank.

## Data model

The scheduler uses four levels:

1. `Block` represents one timed or TBA meeting, such as a Monday/Wednesday
   lecture from 9:00 AM to 10:15 AM.
2. `Section` represents one UCSB enrollment section and contains all of that
   section's meeting blocks.
3. `CourseOption` represents one complete enrollment choice for a course. It
   may contain a required primary section plus one linked secondary section.
4. `Course` contains every selectable `CourseOption` for one course.

This separation matters because a section can meet multiple times, and one
valid course choice can require multiple sections.

## Linking UCSB sections

`backend/course_loader.py` builds options from the official UCSB section
structure. It does not guess based on meeting labels.

- Sections are grouped by course, session, and the first two digits of their
  four-digit section number.
- A section whose `secondary_status` is `R` is the required primary section
  for that family.
- Each eligible child in that family creates one option with the required
  primary section.
- A family without a required primary uses each eligible `xx00` section as a
  standalone option.
- Cancelled sections are never selectable.
- Closed sections are excluded by default and may be included explicitly for
  auditing with `include_closed=True`.
- Ambiguous or malformed families raise `UnresolvedSectionGroupError` instead
  of silently creating a questionable option.

For example, a lecture with six linked discussions becomes six distinct
course options. The optimizer chooses one complete option, not a lecture and
discussion independently.

## Schedule generation

`Scheduler` uses conflict-pruning backtracking rather than creating the full
Cartesian product first. Before adding a course option, it checks that:

- the option does not conflict with itself;
- none of its timed blocks overlap a previously selected option; and
- back-to-back meetings are allowed because their time intervals do not
  overlap.

TBA and untimed blocks remain visible in an option but do not create false
time conflicts.

`combination_limit` rejects requests whose raw option count is too large. This
protects the app from accidentally attempting an impractical search. After a
run, `last_generation_stats` reports the raw, valid, and returned counts.

## Ranking

`rank_schedules(preferences, limit=3)` returns the best schedules in a stable,
deterministic order. Each schedule receives a weighted percentage score based
on:

- preferred meeting days;
- preferred start and end times; and
- the preferred minimum gap between consecutive classes.

Only timed meetings participate in these calculations. A preference category
with no evaluable meetings receives a neutral score, and a request whose total
weight is zero is rejected.

## Winter 2027 validation

The Phase 3 loader was exercised against the complete local Winter 2027
catalog:

- 2,287 courses loaded without an unresolved section family;
- 4,711 structural course options were built when closed sections were
  included for auditing; and
- `PHYS 160K` was the only source course with no structural option.

A real scheduling smoke test using `CMPSC 16`, `MATH 4A`, and `ANTH 102`
produced 96 raw combinations, 80 conflict-free schedules, and three ranked
results.

## Verification

Run the complete backend suite from the repository root:

```bash
PYTHONPATH=backend python3 -m unittest discover -s tests
```

The Phase 3 checkpoint passes 105 tests covering catalog normalization,
database queries, section linking, conflicts, combination limits, scoring,
ranking, TBA meetings, closed and cancelled sections, and deterministic
results.
