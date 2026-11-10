# Winter 2027 Catalog Audit

Quarter code: `20271`

Source: UCSB Academic Curriculums v3 API

## Import Result

The API advertises 2,290 courses but consistently returns 2,287 after a
complete pagination sweep. The same three-record shortfall occurs with page
sizes of 50, 100, and 500. The importer records both totals and accepts only a
bounded source shortfall; larger or incomplete downloads fail before replacing
catalog data.

Imported normalized rows:

- Courses: 2,287
- Sections: 5,167
- Meetings: 4,144
- Meeting-day rows: 5,634
- Instructor assignments: 2,728
- General education requirements: 919
- Concurrent-course records: 701

The generated database is `instance/catalog.db`. It is intentionally ignored
by Git and can be recreated from the API.

## Data Quality Findings

- Closed sections: 299
- Cancelled sections: 13
- Online courses: 17
- Sections without meeting records: 1,092
- Sections with TBA meeting times: 40
- Sections containing multiple required meeting records: 67
- Sections with multiple instructors: 189
- Sections with missing enrollment codes: 0
- Sections whose `secondaryStatus` is `R`: 443

UCSB status values are not consistently typed. `classClosed` uses `Y`,
`courseCancelled` uses `C`, and major/minor restrictions contain encoded text
lists rather than booleans. The normalizer handles these values explicitly and
retains each original source object as JSON.

Duplicate GE rows occur in five courses. Identical `(GE code, college)` pairs
are deduplicated in the queryable table while the complete raw course JSON is
preserved.

## Section-Linking Findings

All 5,167 section numbers are four numeric characters. Most primary sections
end in `00`, and common lecture/secondary families use prefixes such as `01`,
`02`, and `03`. This is useful evidence, but it is not a universal linkage rule:

- 101 courses have multiple lecture sections.
- 144 courses have multiple primary-type sections.
- 646 courses have no section currently classified as a primary type.
- Some secondary components also end in `00`.
- Instruction codes include lecture, discussion, lab, tutorial, seminar,
  studio, fieldwork, colloquium, and records with no code.

Phase 3 must therefore derive course options using course-level section groups
and verified UCSB patterns. It must not cross-product every lecture with every
discussion or assume every `00` section is a lecture.

## Verification

Run the Phase 2 tests:

```bash
PYTHONPATH=backend python3 -m unittest \
  tests.test_ucsb_client \
  tests.test_catalog_normalizer \
  tests.test_catalog_repository \
  tests.test_sync_catalog \
  tests.test_catalog_audit
```

Refresh the catalog:

```bash
python3 backend/sync_catalog.py \
  --quarter 20271 \
  --database instance/catalog.db \
  --page-size 100 \
  --max-source-shortfall 10
```

Run the read-only audit:

```bash
python3 backend/catalog_audit.py \
  --quarter 20271 \
  --database instance/catalog.db
```
