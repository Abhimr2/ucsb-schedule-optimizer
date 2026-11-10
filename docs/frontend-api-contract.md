# Frontend API Contract

Phase 4 exposes the Winter 2027 catalog and Phase 3 scheduler as JSON.

- Base path: `/api/v1`
- Content type: `application/json`
- Active term: `2027-winter`
- UCSB quarter: `20271`
- Time zone: `America/Los_Angeles`

## Conventions

- Course codes are canonical strings such as `CMPSC 16`.
- Days are `M`, `T`, `W`, `R`, and `F`.
- Times are integer minutes after midnight. `540` means 9:00 AM.
- Enrollment codes remain strings so leading zeroes are preserved.
- IDs and pagination cursors are opaque.
- Unknown source values are `null`; the API does not invent catalog data.
- Every response includes an `X-Request-ID` header.

Catalog responses include:

```json
{
  "freshness": {
    "term": "2027-winter",
    "term_label": "Winter 2027",
    "dataset_kind": "snapshot",
    "is_live": false,
    "source": "ucsb_academic_curriculums_api",
    "last_updated_at": "2026-11-09T23:00:00+00:00",
    "expires_at": null
  }
}
```

## Model hierarchy

The response model matches Phase 3:

1. A course contains complete `options`.
2. A `CourseOption` contains one or more compatible `sections`.
3. A `Section` represents one enrollment code.
4. A section contains all of its `meetings`.

A lecture paired with a required discussion is one option containing two
sections. A standalone lecture is one option containing one section. The
frontend must not combine sections or reproduce section-linking rules.

## Endpoints

### `GET /api/v1/status`

Returns service status and catalog freshness.

```json
{
  "status": "ok",
  "api_version": "v1",
  "freshness": {
    "term": "2027-winter",
    "term_label": "Winter 2027",
    "dataset_kind": "snapshot",
    "is_live": false,
    "source": "ucsb_academic_curriculums_api",
    "last_updated_at": "2026-11-09T23:00:00+00:00",
    "expires_at": null
  }
}
```

### `GET /api/v1/courses`

Searches course code, title, subject area, and department.

| Parameter | Required | Rules |
|---|---:|---|
| `q` | Yes | Trimmed text with at least 2 characters |
| `limit` | No | Integer from 1 to 50; default `20` |
| `cursor` | No | Opaque cursor returned by the preceding page |
| `term` | No | Must equal `2027-winter` when supplied |

```json
{
  "items": [
    {
      "code": "CMPSC 16",
      "name": "PROBLEM SOLVING I",
      "term": "2027-winter",
      "units": 4.0,
      "grading_option": null,
      "department": "CMPSC",
      "college": "L&S",
      "online": false,
      "section_count": 12
    }
  ],
  "next_cursor": "opaque-value-or-null",
  "freshness": {}
}
```

`next_cursor` is `null` when there is no later page. A cursor is bound to the
search query and active quarter. Changing `q` starts a new search.

Invalid queries, limits, or cursors return `400 INVALID_REQUEST`. An
unavailable term returns `409 TERM_UNAVAILABLE`.

### `GET /api/v1/courses/{course_code}`

Returns course metadata and complete options. URL encode the course code, as
in `/api/v1/courses/CMPSC%2016`.

```json
{
  "code": "CMPSC 16",
  "name": "PROBLEM SOLVING I",
  "term": "2027-winter",
  "term_label": "Winter 2027",
  "units": 4.0,
  "grading_option": null,
  "description": "Course description from UCSB.",
  "department": "CMPSC",
  "college": "L&S",
  "online": false,
  "general_education": [
    {"ge_code": "C", "ge_college": "L&S"}
  ],
  "option_count": 1,
  "options": [
    {
      "id": "2027-winter:CMPSC 16:option:08128:08144",
      "index": 0,
      "enroll_codes": ["08128", "08144"],
      "section_numbers": ["0100", "0101"],
      "available": true,
      "has_tba": false,
      "is_incomplete": false,
      "sections": [
        {
          "enroll_code": "08128",
          "section_number": "0100",
          "parent_enroll_code": null,
          "instruction_type": "lecture",
          "session": null,
          "secondary_status": "R",
          "availability": "open",
          "enrollment": {
            "enrolled": 47,
            "capacity": 73,
            "available": 26
          },
          "instructors": ["MAJEDI M"],
          "locations": [
            {"building": "ILP", "room": "1203", "label": "ILP 1203"}
          ],
          "has_tba": false,
          "is_incomplete": false,
          "meetings": [
            {
              "id": "2027-winter:CMPSC 16:08128:0",
              "course_code": "CMPSC 16",
              "course_name": "PROBLEM SOLVING I",
              "enroll_code": "08128",
              "section_number": "0100",
              "parent_enroll_code": null,
              "meeting_type": "lecture",
              "days": ["M", "W"],
              "start_time": 930,
              "end_time": 1005,
              "instructors": ["MAJEDI M"],
              "location": {
                "building": "ILP",
                "room": "1203",
                "label": "ILP 1203"
              },
              "is_tba": false,
              "availability": "open"
            }
          ]
        }
      ]
    }
  ],
  "freshness": {}
}
```

TBA meetings remain in the response. They have `is_tba: true`, and their
days or times may be empty or `null`. They do not create time conflicts.

Course detail may show closed options with `available: false` so the frontend
can explain availability. Schedule generation excludes closed and cancelled
sections. An unknown code returns `404 COURSE_NOT_FOUND`.

### `POST /api/v1/schedules/generate`

Generates conflict-free schedules and ranks them with the Phase 3 scoring
model.

```json
{
  "term": "2027-winter",
  "course_codes": ["CMPSC 16", "MATH 4A", "ANTH 102"],
  "preferences": {
    "preferred_days": ["M", "W", "F"],
    "preferred_start": 540,
    "preferred_end": 1020,
    "minimum_gap": 60,
    "weights": {
      "days": 2,
      "time_range": 2,
      "gaps": 1
    }
  },
  "result_limit": 3
}
```

`term` is optional and defaults to the active term.

Validation:

- `course_codes` contains 2 to 5 unique courses from the active catalog.
- `preferred_days` is a unique subset of `M`, `T`, `W`, `R`, and `F`.
- `preferred_start` and `preferred_end` are integers from 0 to 1439, and the
  start is earlier than the end.
- `minimum_gap` is an integer from 15 to 720.
- Each weight is an integer from 0 to 3, with at least one positive weight.
- `result_limit` is an integer from 1 to 10 and defaults to 3.

```json
{
  "request_id": "sched_opaque_id",
  "term": "2027-winter",
  "raw_schedule_count": 96,
  "valid_schedule_count": 80,
  "returned_schedule_count": 3,
  "schedules": [
    {
      "id": "sched_opaque_id:1",
      "rank": 1,
      "score": 94.4444,
      "score_breakdown": {
        "days": {"ratio": 1.0, "weight": 2},
        "time_range": {"ratio": 0.833333, "weight": 2},
        "gaps": {"ratio": 1.0, "weight": 1}
      },
      "summary": {
        "class_days": 4,
        "days": ["M", "T", "W", "F"],
        "earliest_start": 540,
        "latest_end": 1005,
        "minimum_gap": 60,
        "outside_time_range_count": 0,
        "non_preferred_day_count": 1,
        "tba_meeting_count": 0
      },
      "course_options": [
        {
          "course_code": "CMPSC 16",
          "course_name": "PROBLEM SOLVING I",
          "option_id": "2027-winter:CMPSC 16:option:08128:08144",
          "section_numbers": ["0100", "0101"],
          "enroll_codes": ["08128", "08144"],
          "meeting_ids": [
            "2027-winter:CMPSC 16:08128:0",
            "2027-winter:CMPSC 16:08144:0"
          ]
        }
      ],
      "meetings": []
    }
  ],
  "freshness": {}
}
```

The `meetings` array uses the same meeting shape as course detail and is ready
for calendar rendering. Schedules are ordered by descending score with stable
tie-breaking.

The schedule `request_id` identifies a deterministic generation request. It
is separate from the per-HTTP-request `X-Request-ID` header.

No conflict-free combination is a successful result:

```json
{
  "request_id": "sched_opaque_id",
  "term": "2027-winter",
  "raw_schedule_count": 12,
  "valid_schedule_count": 0,
  "returned_schedule_count": 0,
  "schedules": [],
  "freshness": {}
}
```

## Errors

Every non-2xx response uses:

```json
{
  "error": {
    "code": "INVALID_PREFERENCES",
    "message": "The schedule preferences are invalid.",
    "field_errors": {
      "preferences.preferred_end": "Must be greater than preferred_start."
    },
    "request_id": "req_opaque_id",
    "retryable": false
  }
}
```

| HTTP | Code | Use |
|---:|---|---|
| 400 | `INVALID_REQUEST` | Malformed JSON, unsupported field, or invalid query/body value |
| 404 | `COURSE_NOT_FOUND` | Course detail code is unknown |
| 404 | `NOT_FOUND` | API route is unknown |
| 405 | `METHOD_NOT_ALLOWED` | Route does not support the method |
| 409 | `TERM_UNAVAILABLE` | Requested term is not active |
| 422 | `INVALID_COURSE_SELECTION` | Course count, uniqueness, or lookup failed |
| 422 | `INVALID_PREFERENCES` | Preference validation failed |
| 422 | `COMBINATION_LIMIT_EXCEEDED` | Selection creates too many combinations |
| 500 | `INTERNAL_ERROR` | Unexpected server failure |
| 503 | `DATA_UNAVAILABLE` | Catalog database cannot be read |

The body request ID matches the `X-Request-ID` header for errors. Error
responses never expose SQL, secrets, stack traces, or filesystem paths.

## CORS

Development CORS is enabled for:

- `http://localhost:5173`
- `http://127.0.0.1:5173`
- `http://localhost:5180`
- `http://127.0.0.1:5180`

Preflight responses allow `GET`, `POST`, and `OPTIONS`, permit the
`Content-Type` header, echo an approved origin, and include `Vary: Origin`.
Other origins do not receive `Access-Control-Allow-Origin`.

## Frontend rules

- Debounce searches and cancel stale requests.
- Start a new search when `q` changes; do not reuse the old cursor.
- Cache course detail by term and course code.
- Do not implement option linking, conflicts, scoring, or ranking in the UI.
- Render schedule `meetings`; retain `course_options` for enrollment details.
- Display the HTTP request ID with unexpected errors.
