import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import api as api_module
from api import create_app
from catalog_database import connect_catalog, migrate_catalog
from catalog_repository import replace_quarter_catalog


QUARTER = "20271"
API_TERM = "2027-winter"


def make_meeting(days, start_time, end_time, building, room):
    return {
        "ordinal": 0,
        "days": list(days),
        "days_raw": " ".join(days),
        "begin_time": f"{start_time // 60:02}:{start_time % 60:02}",
        "end_time": f"{end_time // 60:02}:{end_time % 60:02}",
        "start_minutes": start_time,
        "end_minutes": end_time,
        "building": building,
        "room": room,
        "room_capacity": 40,
        "is_tba": False,
    }


def make_section(
    enroll_code,
    section_number,
    instruction_type,
    days,
    start_time,
    end_time,
    source_order,
    secondary_status=None,
):
    return {
        "enroll_code": enroll_code,
        "section_number": section_number,
        "session": None,
        "instruction_type_raw": instruction_type.upper(),
        "instruction_type": instruction_type,
        "class_closed": False,
        "course_cancelled": False,
        "grading_option_code": None,
        "enrolled_total": 12,
        "max_enroll": 40,
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
        "meetings": [
            make_meeting(days, start_time, end_time, "ILP", str(1200 + source_order))
        ],
        "instructors": [
            {
                "ordinal": 0,
                "name": f"Instructor {source_order + 1}",
                "function_code": "Teaching and in charge",
            }
        ],
        "concurrent_courses": [],
    }


def make_course(course_id, title, department, sections, source_order):
    return {
        "course": {
            "quarter": QUARTER,
            "course_id": course_id,
            "raw_course_id": f"{course_id}  ",
            "title": title,
            "description": f"Description for {course_id}",
            "subject_area": department,
            "department_code": department,
            "college": "L&S",
            "level_code": "U",
            "contact_hours": 30.0,
            "units_fixed": 4.0,
            "units_variable_low": None,
            "units_variable_high": None,
            "grading_option": "Letter",
            "instruction_type": "LEC",
            "online": False,
            "source_order": source_order,
            "source_json": {"courseId": course_id},
        },
        "general_education": [{"ge_code": "C", "ge_college": "L&S"}],
        "sections": sections,
    }


def fixture_catalog():
    cmpsc_sections = [
        make_section(
            "08128",
            "0100",
            "lecture",
            ("M", "W"),
            540,
            600,
            0,
            secondary_status="R",
        ),
        make_section(
            "08144",
            "0101",
            "discussion",
            ("F",),
            600,
            650,
            1,
        ),
        make_section(
            "08151",
            "0102",
            "discussion",
            ("F",),
            660,
            710,
            2,
        ),
    ]
    math_sections = [
        make_section(
            "30106",
            "0100",
            "lecture",
            ("T", "R"),
            540,
            615,
            0,
        )
    ]
    conflict_sections = [
        make_section(
            "50001",
            "0100",
            "lecture",
            ("M",),
            570,
            630,
            0,
        )
    ]

    return [
        make_course(
            "CMPSC 16",
            "PROBLEM SOLVING I",
            "CMPSC",
            cmpsc_sections,
            0,
        ),
        make_course(
            "MATH 4A",
            "LINEAR ALGEBRA WITH APPLICATIONS",
            "MATH",
            math_sections,
            1,
        ),
        make_course(
            "PSTAT 1",
            "MATHEMATICAL CONFLICTING COURSE",
            "PSTAT",
            conflict_sections,
            2,
        ),
    ]


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "catalog.db"
        self.frontend_dist = Path(self.temporary_directory.name) / "dist"
        (self.frontend_dist / "assets").mkdir(parents=True)
        (self.frontend_dist / "index.html").write_text(
            "<html><body>Gaucho Route Builder</body></html>",
            encoding="utf-8",
        )
        (self.frontend_dist / "assets" / "app-test.js").write_text(
            "console.log('ready')",
            encoding="utf-8",
        )
        connection = connect_catalog(self.database_path)
        try:
            migrate_catalog(connection)
            catalog = fixture_catalog()
            replace_quarter_catalog(connection, QUARTER, catalog, len(catalog))
        finally:
            connection.close()

        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE_PATH": str(self.database_path),
                "ACTIVE_QUARTER": QUARTER,
                "API_TERM": API_TERM,
                "TERM_LABEL": "Winter 2027",
                "FRONTEND_DIST_PATH": str(self.frontend_dist),
            }
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def schedule_request(self, course_codes=None):
        return {
            "course_codes": course_codes or ["CMPSC 16", "MATH 4A"],
            "preferences": {
                "preferred_days": ["M", "T", "W", "R", "F"],
                "preferred_start": 480,
                "preferred_end": 1080,
                "minimum_gap": 60,
                "weights": {"days": 1, "time_range": 1, "gaps": 1},
            },
            "result_limit": 3,
        }

    def assert_error(self, response, status, code):
        self.assertEqual(status, response.status_code)
        payload = response.get_json()
        self.assertEqual({"error"}, set(payload))
        error = payload["error"]
        self.assertEqual(
            {"code", "message", "field_errors", "request_id", "retryable"},
            set(error),
        )
        self.assertEqual(code, error["code"])
        self.assertIsInstance(error["message"], str)
        self.assertTrue(error["message"])
        self.assertIsInstance(error["field_errors"], dict)
        self.assertIsInstance(error["request_id"], str)
        self.assertTrue(error["request_id"])
        self.assertIsInstance(error["retryable"], bool)
        self.assertEqual(error["request_id"], response.headers["X-Request-ID"])
        return error

    def test_status_uses_configured_term_and_catalog(self):
        response = self.client.get("/api/v1/status")

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual("ok", payload["status"])
        self.assertEqual("v1", payload["api_version"])
        self.assertEqual(API_TERM, payload["freshness"]["term"])
        self.assertEqual("Winter 2027", payload["freshness"]["term_label"])
        self.assertEqual("snapshot", payload["freshness"]["dataset_kind"])
        self.assertFalse(payload["freshness"]["is_live"])
        self.assertTrue(response.headers["X-Request-ID"])

    def test_frontend_index_assets_and_client_routes(self):
        index_response = self.client.get("/")
        route_response = self.client.get("/schedule/results")
        asset_response = self.client.get("/assets/app-test.js")

        self.assertEqual(200, index_response.status_code)
        self.assertIn("Gaucho Route Builder", index_response.get_data(as_text=True))
        self.assertEqual(index_response.get_data(), route_response.get_data())
        self.assertEqual("console.log('ready')", asset_response.get_data(as_text=True))
        self.assertEqual("no-cache", index_response.headers["Cache-Control"])
        self.assertIn("immutable", asset_response.headers["Cache-Control"])

        for response in (index_response, route_response, asset_response):
            self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
            self.assertEqual("DENY", response.headers["X-Frame-Options"])
            self.assertEqual(
                "camera=(), microphone=(), geolocation=()",
                response.headers["Permissions-Policy"],
            )
            response.close()

    def test_unknown_api_route_uses_error_envelope(self):
        self.assert_error(
            self.client.get("/api/v1/not-a-route"),
            404,
            "NOT_FOUND",
        )

    def test_course_search_and_no_results(self):
        response = self.client.get("/api/v1/courses?q=problem&limit=10")

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(API_TERM, payload["freshness"]["term"])
        self.assertEqual("Winter 2027", payload["freshness"]["term_label"])
        self.assertIsNone(payload["next_cursor"])
        self.assertEqual(1, len(payload["items"]))
        summary = payload["items"][0]
        self.assertEqual("CMPSC 16", summary["code"])
        self.assertEqual("PROBLEM SOLVING I", summary["name"])
        self.assertEqual("CMPSC", summary["department"])
        self.assertEqual(3, summary["section_count"])
        self.assertEqual(4.0, summary["units"])

        empty_response = self.client.get("/api/v1/courses?q=does-not-exist")
        self.assertEqual(200, empty_response.status_code)
        self.assertEqual([], empty_response.get_json()["items"])
        self.assertIsNone(empty_response.get_json()["next_cursor"])

    def test_course_search_uses_opaque_cursor(self):
        first_response = self.client.get("/api/v1/courses?q=ma&limit=1")
        self.assertEqual(200, first_response.status_code)
        first_payload = first_response.get_json()
        self.assertEqual(1, len(first_payload["items"]))
        self.assertIsInstance(first_payload["next_cursor"], str)
        self.assertTrue(first_payload["next_cursor"])

        second_response = self.client.get(
            "/api/v1/courses",
            query_string={
                "q": "ma",
                "limit": 1,
                "cursor": first_payload["next_cursor"],
            },
        )
        self.assertEqual(200, second_response.status_code)
        second_payload = second_response.get_json()
        self.assertEqual(1, len(second_payload["items"]))
        self.assertNotEqual(
            first_payload["items"][0]["code"],
            second_payload["items"][0]["code"],
        )

    def test_course_search_rejects_invalid_parameters(self):
        cases = (
            ("?q=x", "q"),
            ("?q=ma&limit=not-a-number", "limit"),
            ("?q=ma&limit=0", "limit"),
            ("?q=ma&limit=51", "limit"),
            ("?q=ma&cursor=not-a-valid-cursor", "cursor"),
        )

        for query, field in cases:
            with self.subTest(query=query):
                error = self.assert_error(
                    self.client.get(f"/api/v1/courses{query}"),
                    400,
                    "INVALID_REQUEST",
                )
                self.assertIn(field, error["field_errors"])

    def test_course_detail_preserves_options_sections_and_meetings(self):
        response = self.client.get("/api/v1/courses/CMPSC%2016")

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual("CMPSC 16", payload["code"])
        self.assertEqual("PROBLEM SOLVING I", payload["name"])
        self.assertEqual(API_TERM, payload["term"])
        self.assertEqual(2, len(payload["options"]))

        first_option = payload["options"][0]
        self.assertIsInstance(first_option["id"], str)
        self.assertEqual(2, len(first_option["sections"]))
        primary = first_option["sections"][0]
        secondary = first_option["sections"][1]
        self.assertEqual("08128", primary["enroll_code"])
        self.assertEqual("lecture", primary["instruction_type"])
        self.assertEqual("R", primary["secondary_status"])
        self.assertEqual("08144", secondary["enroll_code"])
        self.assertEqual("discussion", secondary["instruction_type"])
        self.assertEqual(["M", "W"], primary["meetings"][0]["days"])
        self.assertEqual(540, primary["meetings"][0]["start_time"])
        self.assertEqual(600, primary["meetings"][0]["end_time"])
        self.assertEqual("open", primary["availability"])
        self.assertEqual(12, primary["enrollment"]["enrolled"])
        self.assertEqual(40, primary["enrollment"]["capacity"])
        self.assertEqual(28, primary["enrollment"]["available"])

    def test_unknown_course_uses_error_envelope(self):
        error = self.assert_error(
            self.client.get("/api/v1/courses/UNKNOWN%2099"),
            404,
            "COURSE_NOT_FOUND",
        )
        self.assertEqual({}, error["field_errors"])

    def test_generate_returns_ranked_schedule(self):
        response = self.client.post(
            "/api/v1/schedules/generate",
            json=self.schedule_request(),
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(API_TERM, payload["term"])
        self.assertEqual(API_TERM, payload["freshness"]["term"])
        self.assertEqual(2, payload["raw_schedule_count"])
        self.assertEqual(2, payload["valid_schedule_count"])
        self.assertEqual(2, payload["returned_schedule_count"])
        self.assertEqual(2, len(payload["schedules"]))
        self.assertTrue(payload["request_id"].startswith("sched_"))
        self.assertTrue(response.headers["X-Request-ID"].startswith("req_"))

        best = payload["schedules"][0]
        self.assertEqual(1, best["rank"])
        self.assertGreaterEqual(best["score"], 0)
        self.assertLessEqual(best["score"], 100)
        self.assertEqual(2, len(best["course_options"]))
        self.assertEqual(
            {"CMPSC 16", "MATH 4A"},
            {option["course_code"] for option in best["course_options"]},
        )
        self.assertGreaterEqual(len(best["meetings"]), 3)
        meeting = best["meetings"][0]
        self.assertIn("course_code", meeting)
        self.assertIn("enroll_code", meeting)
        self.assertIn("days", meeting)
        self.assertIn("start_time", meeting)
        self.assertIn("end_time", meeting)

    def test_generate_returns_success_when_every_option_conflicts(self):
        response = self.client.post(
            "/api/v1/schedules/generate",
            json=self.schedule_request(["CMPSC 16", "PSTAT 1"]),
        )

        self.assertEqual(200, response.status_code)
        payload = response.get_json()
        self.assertEqual(2, payload["raw_schedule_count"])
        self.assertEqual(0, payload["valid_schedule_count"])
        self.assertEqual(0, payload["returned_schedule_count"])
        self.assertEqual([], payload["schedules"])

    def test_generate_rejects_malformed_json_and_non_object_body(self):
        malformed = self.client.post(
            "/api/v1/schedules/generate",
            data="{",
            content_type="application/json",
        )
        malformed_error = self.assert_error(
            malformed,
            400,
            "INVALID_REQUEST",
        )
        self.assertIn("body", malformed_error["field_errors"])

        non_object = self.client.post(
            "/api/v1/schedules/generate",
            json=["CMPSC 16", "MATH 4A"],
        )
        non_object_error = self.assert_error(
            non_object,
            400,
            "INVALID_REQUEST",
        )
        self.assertIn("body", non_object_error["field_errors"])

    def test_generate_validates_course_selection(self):
        cases = (
            (["CMPSC 16"], "two and five"),
            (["CMPSC 16", "CMPSC 16"], "unique"),
            (["CMPSC 16", "UNKNOWN 99"], "unknown course code"),
        )

        for course_codes, message_fragment in cases:
            with self.subTest(course_codes=course_codes):
                error = self.assert_error(
                    self.client.post(
                        "/api/v1/schedules/generate",
                        json=self.schedule_request(course_codes),
                    ),
                    422,
                    "INVALID_COURSE_SELECTION",
                )
                matching_messages = [
                    message
                    for key, message in error["field_errors"].items()
                    if key == "course_codes" or key.startswith("course_codes.")
                ]
                self.assertTrue(matching_messages)
                self.assertTrue(
                    any(
                        message_fragment.lower() in message.lower()
                        for message in matching_messages
                    )
                )

    def test_generate_validates_preferences(self):
        invalid_preferences = (
            ("preferred_days", ["M", "X"]),
            ("preferred_start", 1200),
            ("minimum_gap", 14),
            ("weights", {"days": 0, "time_range": 0, "gaps": 0}),
        )

        for field, value in invalid_preferences:
            with self.subTest(field=field):
                payload = self.schedule_request()
                payload["preferences"][field] = value
                if field == "preferred_start":
                    payload["preferences"]["preferred_end"] = 600
                error = self.assert_error(
                    self.client.post(
                        "/api/v1/schedules/generate",
                        json=payload,
                    ),
                    422,
                    "INVALID_PREFERENCES",
                )
                self.assertTrue(
                    any(
                        key == "preferences"
                        or key.startswith("preferences.")
                        for key in error["field_errors"]
                    )
                )

    def test_localhost_preflight_includes_cors_headers(self):
        response = self.client.options(
            "/api/v1/schedules/generate",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )

        self.assertIn(response.status_code, (200, 204))
        self.assertEqual(
            "http://localhost:5173",
            response.headers["Access-Control-Allow-Origin"],
        )
        self.assertIn("POST", response.headers["Access-Control-Allow-Methods"])
        self.assertIn(
            "Content-Type",
            response.headers["Access-Control-Allow-Headers"],
        )
        self.assertIn("Origin", response.headers["Vary"])

    def test_internal_error_does_not_leak_database_path(self):
        leaked_value = f"failed to open {self.database_path}"

        with mock.patch.object(
            api_module,
            "_service",
            side_effect=RuntimeError(leaked_value),
        ):
            with self.assertLogs(self.app.logger, level="ERROR"):
                response = self.client.get("/api/v1/status")

        error = self.assert_error(response, 500, "INTERNAL_ERROR")
        body = response.get_data(as_text=True)
        self.assertNotIn(str(self.database_path), body)
        self.assertNotIn(leaked_value, body)
        self.assertTrue(error["retryable"])


if __name__ == "__main__":
    unittest.main()
