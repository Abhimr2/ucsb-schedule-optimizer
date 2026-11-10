import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from api_service import ApiError, CatalogService
from catalog_database import connect_catalog, migrate_catalog
from catalog_repository import replace_quarter_catalog


def make_meeting(
    days=("M", "W"),
    start=540,
    end=590,
    building="ILP",
    room="1203",
    is_tba=False,
    ordinal=0,
):
    return {
        "ordinal": ordinal,
        "days": list(days),
        "days_raw": " ".join(days) if days else None,
        "begin_time": None if start is None else f"{start // 60:02d}:{start % 60:02d}",
        "end_time": None if end is None else f"{end // 60:02d}:{end % 60:02d}",
        "start_minutes": start,
        "end_minutes": end,
        "building": building,
        "room": room,
        "room_capacity": 60,
        "is_tba": is_tba,
    }


def make_section(
    enroll_code,
    section_number,
    instruction_type="lecture",
    secondary_status=None,
    meetings=None,
    instructors=("ADA LOVELACE",),
    closed=False,
    cancelled=False,
    source_order=0,
):
    if meetings is None:
        meetings = [make_meeting()]

    return {
        "enroll_code": enroll_code,
        "section_number": section_number,
        "session": None,
        "instruction_type_raw": instruction_type.upper(),
        "instruction_type": instruction_type,
        "class_closed": closed,
        "course_cancelled": cancelled,
        "grading_option_code": None,
        "enrolled_total": 20,
        "max_enroll": 60,
        "secondary_status": secondary_status,
        "department_approval_required": False,
        "instructor_approval_required": False,
        "restriction_level": None,
        "restriction_major": None,
        "restriction_major_pass": None,
        "restriction_minor": None,
        "restriction_minor_pass": None,
        "source_order": source_order,
        "source_json": {"enrollCode": enroll_code},
        "meetings": meetings,
        "instructors": [
            {
                "ordinal": index,
                "name": name,
                "function_code": "Teaching and in charge",
            }
            for index, name in enumerate(instructors)
        ],
        "concurrent_courses": [],
    }


def make_course(
    course_id,
    title,
    sections,
    source_order,
    units=4.0,
    grading_option="Letter Graded",
    online=False,
):
    department = course_id.rsplit(" ", 1)[0]
    return {
        "course": {
            "quarter": "20271",
            "course_id": course_id,
            "raw_course_id": f"{course_id}  ",
            "title": title,
            "description": f"Description for {course_id}",
            "subject_area": department,
            "department_code": department,
            "college": "L&S",
            "level_code": "U",
            "contact_hours": 30.0,
            "units_fixed": units,
            "units_variable_low": None,
            "units_variable_high": None,
            "grading_option": grading_option,
            "instruction_type": "LEC",
            "online": online,
            "source_order": source_order,
            "source_json": {"courseId": course_id},
        },
        "general_education": [{"ge_code": "C", "ge_college": "L&S"}],
        "sections": sections,
    }


def fixture_catalog():
    cmpsc_sections = [
        make_section(
            "10000",
            "0100",
            secondary_status="R",
            meetings=[make_meeting(("M", "W"), 540, 590)],
            source_order=0,
        ),
        make_section(
            "10001",
            "0101",
            instruction_type="discussion",
            meetings=[make_meeting(("T",), 660, 710, "PHELP", "3525")],
            source_order=1,
        ),
        make_section(
            "10002",
            "0102",
            instruction_type="discussion",
            meetings=[make_meeting(("F",), 780, 830, "PHELP", "3525")],
            instructors=(),
            source_order=2,
        ),
    ]
    math_sections = [
        make_section(
            "20000",
            "0100",
            meetings=[make_meeting(("M", "W", "F"), 600, 650, "GIRV", "1115")],
            source_order=0,
        ),
        make_section(
            "20001",
            "0200",
            meetings=[make_meeting(("T", "R"), 480, 555, "GIRV", "1116")],
            source_order=1,
        ),
    ]
    astro_sections = [
        make_section(
            "30000",
            "0100",
            meetings=[
                make_meeting(
                    (),
                    None,
                    None,
                    None,
                    None,
                    is_tba=True,
                )
            ],
            instructors=(),
        )
    ]
    cmpsc_ten_sections = [
        make_section(
            "40000",
            "0100",
            meetings=[make_meeting(("T", "R"), 840, 915)],
        )
    ]

    return [
        make_course("CMPSC 16", "PROBLEM SOLVING I", cmpsc_sections, 0),
        make_course("MATH 4A", "LINEAR ALGEBRA", math_sections, 1),
        make_course("ASTRO 1", "BASIC ASTRONOMY", astro_sections, 2, units=5.0),
        make_course("CMPSC 10", "INTRO COMPUTING", cmpsc_ten_sections, 3),
    ]


def valid_payload(course_codes=None):
    return {
        "term": "2027-winter",
        "course_codes": course_codes or ["CMPSC 16", "MATH 4A"],
        "preferences": {
            "preferred_days": ["M", "W", "F"],
            "preferred_start": 480,
            "preferred_end": 1020,
            "minimum_gap": 60,
            "weights": {
                "days": 2,
                "time_range": 3,
                "gaps": 1,
            },
        },
        "result_limit": 3,
    }


class ApiErrorTests(unittest.TestCase):
    def test_error_exposes_transport_fields(self):
        error = ApiError(
            422,
            "INVALID_TEST",
            "Invalid test.",
            {"field": "Bad value."},
            True,
        )

        self.assertEqual(422, error.status_code)
        self.assertEqual("INVALID_TEST", error.code)
        self.assertEqual("Invalid test.", error.message)
        self.assertEqual({"field": "Bad value."}, error.field_errors)
        self.assertTrue(error.retryable)


class CatalogServiceTests(unittest.TestCase):
    def setUp(self):
        self.connection = connect_catalog(":memory:")
        migrate_catalog(self.connection)
        replace_quarter_catalog(
            self.connection,
            "20271",
            fixture_catalog(),
            4,
        )
        self.service = CatalogService(self.connection)

    def tearDown(self):
        self.connection.close()

    def assert_api_error(self, status_code, code, function, *args):
        with self.assertRaises(ApiError) as context:
            function(*args)
        self.assertEqual(status_code, context.exception.status_code)
        self.assertEqual(code, context.exception.code)
        return context.exception

    def test_status_uses_latest_completed_sync(self):
        self.connection.execute(
            """
            INSERT INTO catalog_sync_runs (
                quarter,
                started_at,
                finished_at,
                expected_total,
                fetched_total,
                status,
                error_message
            )
            VALUES (?, ?, ?, ?, ?, 'completed', NULL)
            """,
            ("20271", "2026-11-10T01:00:00+00:00", "2026-11-10T01:01:00+00:00", 4, 4),
        )
        self.connection.execute(
            """
            INSERT INTO catalog_sync_runs (
                quarter,
                started_at,
                finished_at,
                expected_total,
                fetched_total,
                status,
                error_message
            )
            VALUES (?, ?, ?, ?, ?, 'failed', ?)
            """,
            ("20271", "2026-11-10T02:00:00+00:00", "2026-11-10T02:01:00+00:00", 4, 0, "failed"),
        )
        self.connection.commit()

        payload = self.service.status_payload()

        self.assertEqual("ok", payload["status"])
        self.assertEqual("v1", payload["api_version"])
        self.assertEqual("2027-winter", payload["freshness"]["term"])
        self.assertEqual("snapshot", payload["freshness"]["dataset_kind"])
        self.assertFalse(payload["freshness"]["is_live"])
        self.assertEqual(
            "ucsb_academic_curriculums_api",
            payload["freshness"]["source"],
        )
        self.assertEqual(
            "2026-11-10T01:01:00+00:00",
            payload["freshness"]["last_updated_at"],
        )

    def test_status_rejects_missing_snapshot(self):
        connection = connect_catalog(":memory:")
        migrate_catalog(connection)
        try:
            service = CatalogService(connection)
            error = self.assert_api_error(
                503,
                "DATA_UNAVAILABLE",
                service.status_payload,
            )
            self.assertTrue(error.retryable)
            self.assertNotIn("database", error.message.lower())
        finally:
            connection.close()

    def test_search_validates_query_and_limit(self):
        for query in (None, "", " a "):
            with self.subTest(query=query):
                self.assert_api_error(
                    400,
                    "INVALID_REQUEST",
                    self.service.search_courses,
                    query,
                )

        for limit in (0, 51, True, 1.5):
            with self.subTest(limit=limit):
                self.assert_api_error(
                    400,
                    "INVALID_REQUEST",
                    self.service.search_courses,
                    "cm",
                    limit,
                )

    def test_search_returns_summary_and_stable_cursor_pages(self):
        first = self.service.search_courses(" cm ", limit=1)
        second = self.service.search_courses(
            "cm",
            limit=1,
            cursor=first["next_cursor"],
        )
        repeated = self.service.search_courses("cm", limit=1)

        self.assertEqual(["CMPSC 10"], [item["code"] for item in first["items"]])
        self.assertEqual(["CMPSC 16"], [item["code"] for item in second["items"]])
        self.assertIsNone(second["next_cursor"])
        self.assertEqual(first["next_cursor"], repeated["next_cursor"])
        self.assertNotIn("CMPSC", first["next_cursor"])

        summary = second["items"][0]
        self.assertEqual("PROBLEM SOLVING I", summary["name"])
        self.assertEqual("2027-winter", summary["term"])
        self.assertEqual(4.0, summary["units"])
        self.assertEqual("Letter Graded", summary["grading_option"])
        self.assertEqual("CMPSC", summary["department"])
        self.assertEqual("L&S", summary["college"])
        self.assertFalse(summary["online"])
        self.assertEqual(3, summary["section_count"])
        self.assertEqual("snapshot", second["freshness"]["dataset_kind"])

    def test_search_rejects_invalid_or_mismatched_cursor(self):
        cursor = self.service.search_courses("cm", limit=1)["next_cursor"]

        for query, token in (("cm", "not-a-cursor"), ("math", cursor)):
            with self.subTest(query=query, token=token):
                error = self.assert_api_error(
                    400,
                    "INVALID_REQUEST",
                    self.service.search_courses,
                    query,
                    1,
                    token,
                )
                self.assertIn("cursor", error.field_errors)

    def test_course_detail_serializes_option_section_meeting_hierarchy(self):
        detail = self.service.course_detail(" cmpsc   16 ")

        self.assertEqual("CMPSC 16", detail["code"])
        self.assertEqual("2027-winter", detail["term"])
        self.assertEqual(2, detail["option_count"])
        self.assertEqual("C", detail["general_education"][0]["ge_code"])

        option = detail["options"][0]
        self.assertEqual(["10000", "10001"], option["enroll_codes"])
        self.assertEqual(["0100", "0101"], option["section_numbers"])
        self.assertEqual(2, len(option["sections"]))

        lecture, discussion = option["sections"]
        self.assertEqual("open", lecture["availability"])
        self.assertEqual(["ADA LOVELACE"], lecture["instructors"])
        self.assertEqual("10000", discussion["parent_enroll_code"])
        self.assertEqual("PHELP 3525", discussion["locations"][0]["label"])
        self.assertEqual(["T"], discussion["meetings"][0]["days"])
        self.assertFalse(discussion["meetings"][0]["is_tba"])
        self.assertEqual("snapshot", detail["freshness"]["dataset_kind"])

    def test_course_detail_serializes_tba_meeting_safely(self):
        detail = self.service.course_detail("ASTRO 1")
        meeting = detail["options"][0]["sections"][0]["meetings"][0]

        self.assertEqual([], meeting["days"])
        self.assertIsNone(meeting["start_time"])
        self.assertIsNone(meeting["end_time"])
        self.assertIsNone(meeting["location"])
        self.assertTrue(meeting["is_tba"])

    def test_course_detail_rejects_unknown_course(self):
        error = self.assert_api_error(
            404,
            "COURSE_NOT_FOUND",
            self.service.course_detail,
            "NOPE 1",
        )
        self.assertEqual({}, error.field_errors)

    def test_generation_returns_deterministic_ranked_schedules(self):
        payload = valid_payload()
        first = self.service.generate_schedules(payload)
        second = self.service.generate_schedules(payload)

        self.assertEqual(first, second)
        self.assertTrue(first["request_id"].startswith("sched_"))
        self.assertEqual(4, first["raw_schedule_count"])
        self.assertEqual(4, first["valid_schedule_count"])
        self.assertEqual(3, first["returned_schedule_count"])
        self.assertEqual([1, 2, 3], [item["rank"] for item in first["schedules"]])
        self.assertEqual(
            sorted(
                [item["score"] for item in first["schedules"]],
                reverse=True,
            ),
            [item["score"] for item in first["schedules"]],
        )

        schedule = first["schedules"][0]
        self.assertEqual(
            {"days", "time_range", "gaps"},
            set(schedule["score_breakdown"]),
        )
        self.assertEqual(2, schedule["score_breakdown"]["days"]["weight"])
        self.assertEqual(2, len(schedule["course_options"]))
        self.assertTrue(schedule["course_options"][0]["section_numbers"])
        self.assertTrue(schedule["course_options"][0]["enroll_codes"])
        self.assertTrue(schedule["meetings"])
        self.assertIn("minimum_gap", schedule["summary"])

    def test_generation_handles_tba_meetings(self):
        payload = valid_payload(["ASTRO 1", "MATH 4A"])
        result = self.service.generate_schedules(payload)
        schedule = result["schedules"][0]
        tba_meeting = next(
            meeting for meeting in schedule["meetings"] if meeting["is_tba"]
        )

        self.assertIsNone(tba_meeting["start_time"])
        self.assertIsNone(tba_meeting["end_time"])
        self.assertEqual([], tba_meeting["days"])
        self.assertEqual(1, schedule["summary"]["tba_meeting_count"])

    def test_empty_preferred_days_is_neutral(self):
        payload = valid_payload()
        payload["preferences"]["preferred_days"] = []

        result = self.service.generate_schedules(payload)

        self.assertEqual(
            1.0,
            result["schedules"][0]["score_breakdown"]["days"]["ratio"],
        )
        self.assertEqual(
            0,
            result["schedules"][0]["summary"]["non_preferred_day_count"],
        )

    def test_generation_rejects_non_object_and_unsupported_fields(self):
        self.assert_api_error(
            400,
            "INVALID_REQUEST",
            self.service.generate_schedules,
            [],
        )

        payload = valid_payload()
        payload["unexpected"] = True
        error = self.assert_api_error(
            400,
            "INVALID_REQUEST",
            self.service.generate_schedules,
            payload,
        )
        self.assertIn("unexpected", error.field_errors)

    def test_generation_rejects_invalid_course_selections(self):
        selections = (
            ["CMPSC 16"],
            ["CMPSC 16", "cmpsc 16"],
            ["CMPSC 16", "MISSING 1"],
            ["CMPSC 16", 12],
        )

        for course_codes in selections:
            with self.subTest(course_codes=course_codes):
                payload = valid_payload(course_codes)
                error = self.assert_api_error(
                    422,
                    "INVALID_COURSE_SELECTION",
                    self.service.generate_schedules,
                    payload,
                )
                self.assertTrue(error.field_errors)

    def test_generation_rejects_invalid_days_times_gaps_and_weights(self):
        cases = (
            ("preferred_days", ["M", "M"]),
            ("preferred_days", ["S"]),
            ("preferred_start", True),
            ("preferred_start", -1),
            ("preferred_end", 1440),
            ("minimum_gap", 14),
            ("minimum_gap", 721),
        )

        for field, value in cases:
            with self.subTest(field=field, value=value):
                payload = valid_payload()
                payload["preferences"][field] = value
                self.assert_api_error(
                    422,
                    "INVALID_PREFERENCES",
                    self.service.generate_schedules,
                    payload,
                )

        payload = valid_payload()
        payload["preferences"]["preferred_start"] = 600
        payload["preferences"]["preferred_end"] = 600
        self.assert_api_error(
            422,
            "INVALID_PREFERENCES",
            self.service.generate_schedules,
            payload,
        )

        for weights in (
            {"days": 0, "time_range": 0, "gaps": 0},
            {"days": 4, "time_range": 1, "gaps": 1},
            {"days": 1, "time_range": 1},
            {"days": True, "time_range": 1, "gaps": 1},
        ):
            with self.subTest(weights=weights):
                payload = valid_payload()
                payload["preferences"]["weights"] = weights
                self.assert_api_error(
                    422,
                    "INVALID_PREFERENCES",
                    self.service.generate_schedules,
                    payload,
                )

    def test_generation_rejects_invalid_term_and_result_limit(self):
        payload = valid_payload()
        payload["term"] = "2027-spring"
        self.assert_api_error(
            409,
            "TERM_UNAVAILABLE",
            self.service.generate_schedules,
            payload,
        )

        for result_limit in (0, 11, True, "3"):
            with self.subTest(result_limit=result_limit):
                payload = valid_payload()
                payload["result_limit"] = result_limit
                self.assert_api_error(
                    400,
                    "INVALID_REQUEST",
                    self.service.generate_schedules,
                    payload,
                )

    def test_generation_translates_combination_limit(self):
        service = CatalogService(self.connection, combination_limit=3)

        error = self.assert_api_error(
            422,
            "COMBINATION_LIMIT_EXCEEDED",
            service.generate_schedules,
            valid_payload(),
        )

        self.assertIn("4 combinations", error.field_errors["course_codes"])
        self.assertFalse(error.retryable)


if __name__ == "__main__":
    unittest.main()
