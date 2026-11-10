import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from catalog_database import connect_catalog, migrate_catalog
from catalog_repository import (
    get_catalog_counts,
    get_course_sections,
    replace_quarter_catalog,
    search_courses,
)


def make_course(
    course_id="CMPSC 16",
    quarter="20271",
    title="PROBLEM SOLVING I",
    department_code="CMPSC",
    enroll_code="08128",
    section_number="0100",
    general_education=None,
):
    if general_education is None:
        general_education = [{"ge_code": "C", "ge_college": "L&S"}]

    return {
        "course": {
            "quarter": quarter,
            "course_id": course_id,
            "raw_course_id": f"{course_id}  ",
            "title": title,
            "description": "Fixture course",
            "subject_area": department_code,
            "department_code": department_code,
            "college": "L&S",
            "level_code": "U",
            "contact_hours": 30.0,
            "units_fixed": 4.0,
            "units_variable_low": None,
            "units_variable_high": None,
            "grading_option": None,
            "instruction_type": "LEC",
            "online": False,
            "source_order": 0,
            "source_json": {"courseId": course_id},
        },
        "general_education": general_education,
        "sections": [
            {
                "enroll_code": enroll_code,
                "section_number": section_number,
                "session": None,
                "instruction_type_raw": "LEC",
                "instruction_type": "lecture",
                "class_closed": False,
                "course_cancelled": False,
                "grading_option_code": None,
                "enrolled_total": 40,
                "max_enroll": 75,
                "secondary_status": "R",
                "department_approval_required": False,
                "instructor_approval_required": False,
                "restriction_level": None,
                "restriction_major": None,
                "restriction_major_pass": None,
                "restriction_minor": None,
                "restriction_minor_pass": None,
                "source_order": 0,
                "source_json": {"enrollCode": enroll_code},
                "meetings": [
                    {
                        "ordinal": 0,
                        "days": ["M", "W"],
                        "days_raw": "M W",
                        "begin_time": "15:30",
                        "end_time": "16:45",
                        "start_minutes": 930,
                        "end_minutes": 1005,
                        "building": "ILP",
                        "room": "1203",
                        "room_capacity": 75,
                        "is_tba": False,
                    }
                ],
                "instructors": [
                    {
                        "ordinal": 0,
                        "name": "M MAJEDI",
                        "function_code": "Teaching and in charge",
                    }
                ],
                "concurrent_courses": [
                    {"ordinal": 0, "value_json": {"courseId": "PSTAT 10"}}
                ],
            }
        ],
    }


class CatalogRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = (
            Path(self.temporary_directory.name) / "catalog.sqlite3"
        )
        self.connection = connect_catalog(self.database_path)
        migrate_catalog(self.connection)

    def tearDown(self):
        self.connection.close()
        self.temporary_directory.cleanup()

    def test_migrations_are_versioned_and_idempotent(self):
        versions = self.connection.execute(
            "SELECT version, name FROM schema_migrations"
        ).fetchall()

        self.assertEqual([(1, "001_catalog.sql")], [tuple(row) for row in versions])
        self.assertEqual([], migrate_catalog(self.connection))
        self.assertEqual(1, self.connection.execute("PRAGMA foreign_keys").fetchone()[0])
        self.assertEqual("wal", self.connection.execute("PRAGMA journal_mode").fetchone()[0])

    def test_migrate_catalog_accepts_a_database_path(self):
        second_path = Path(self.temporary_directory.name) / "nested" / "catalog.db"

        self.assertEqual([1], migrate_catalog(second_path))

        second_connection = connect_catalog(second_path)
        try:
            migration = second_connection.execute(
                "SELECT version, name FROM schema_migrations"
            ).fetchone()
            self.assertEqual((1, "001_catalog.sql"), tuple(migration))
            self.assertIsNotNone(
                second_connection.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table' AND name = 'courses'
                    """
                ).fetchone()
            )
        finally:
            second_connection.close()

    def test_foreign_keys_leading_zeroes_and_nested_lookup(self):
        catalog = [make_course(enroll_code="00123", section_number="0100")]
        replace_quarter_catalog(self.connection, "20271", catalog, 1)

        result = get_course_sections(self.connection, "20271", "CMPSC 16")
        section = result["sections"][0]

        self.assertEqual("00123", section["enroll_code"])
        self.assertEqual("0100", section["section_number"])
        self.assertEqual(["M", "W"], section["meetings"][0]["days"])
        self.assertEqual("M MAJEDI", section["instructors"][0]["name"])
        self.assertEqual(1, len(section["concurrent_courses"]))

        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                INSERT INTO sections (
                    quarter,
                    course_id,
                    enroll_code,
                    section_number,
                    source_order,
                    source_json
                )
                VALUES ('20271', 'MISSING 1', '99999', '0100', 0, '{}')
                """
            )

        self.connection.rollback()

    def test_text_encoded_major_and_minor_restrictions_are_preserved(self):
        catalog = [make_course()]
        major_restrictions = "+CMPSC    &MATH      -PSTAT   "
        minor_restrictions = "&DATA SCI   -ECON     +STAT    "
        section = catalog[0]["sections"][0]
        section["restriction_major"] = major_restrictions
        section["restriction_minor"] = minor_restrictions

        replace_quarter_catalog(self.connection, "20271", catalog, 1)

        result = get_course_sections(self.connection, "20271", "CMPSC 16")
        stored_section = result["sections"][0]

        self.assertEqual(
            major_restrictions,
            stored_section["restriction_major"],
        )
        self.assertEqual(
            minor_restrictions,
            stored_section["restriction_minor"],
        )

    def test_search_returns_lightweight_paginated_summaries(self):
        catalog = [
            make_course(),
            make_course(
                course_id="MATH 4A",
                title="LINEAR ALGEBRA",
                department_code="MATH",
                enroll_code="30106",
            ),
            make_course(
                course_id="ANTH 1",
                title="INTRO HEALTH",
                department_code="ANTH",
                enroll_code="52159",
            ),
        ]
        replace_quarter_catalog(self.connection, "20271", catalog, 3)

        math_results = search_courses(self.connection, "20271", "linear")
        department_results = search_courses(self.connection, "20271", "cmpsc")
        paged_results = search_courses(
            self.connection,
            "20271",
            limit=1,
            offset=1,
        )

        self.assertEqual(["MATH 4A"], [row["course_id"] for row in math_results])
        self.assertEqual(["CMPSC 16"], [row["course_id"] for row in department_results])
        self.assertEqual(1, len(paged_results))
        self.assertIn("section_count", paged_results[0])
        self.assertNotIn("source_json", paged_results[0])
        self.assertNotIn("description", paged_results[0])

    def test_deleting_course_cascades_to_all_children(self):
        replace_quarter_catalog(
            self.connection,
            "20271",
            [make_course()],
            1,
        )

        self.connection.execute(
            "DELETE FROM courses WHERE quarter = '20271' AND course_id = 'CMPSC 16'"
        )
        self.connection.commit()

        counts = get_catalog_counts(self.connection, "20271")
        self.assertEqual(
            {
                "courses": 0,
                "sections": 0,
                "meetings": 0,
                "instructors": 0,
                "general_education": 0,
            },
            counts,
        )
        self.assertEqual(
            0,
            self.connection.execute(
                "SELECT COUNT(*) FROM meeting_days"
            ).fetchone()[0],
        )
        self.assertEqual(
            0,
            self.connection.execute(
                "SELECT COUNT(*) FROM section_concurrent_courses"
            ).fetchone()[0],
        )

    def test_replacing_same_quarter_is_idempotent(self):
        catalog = [make_course()]

        first = replace_quarter_catalog(self.connection, "20271", catalog, 1)
        second = replace_quarter_catalog(self.connection, "20271", catalog, 1)

        for key in (
            "courses",
            "sections",
            "meetings",
            "instructors",
            "general_education",
        ):
            self.assertEqual(first[key], second[key])

        self.assertEqual(
            2,
            self.connection.execute(
                """
                SELECT COUNT(*)
                FROM catalog_sync_runs
                WHERE quarter = '20271' AND status = 'completed'
                """
            ).fetchone()[0],
        )

    def test_completed_sync_preserves_advertised_and_fetched_totals(self):
        catalog = [
            make_course(),
            make_course(
                course_id="ANTH 1",
                title="INTRO HEALTH",
                department_code="ANTH",
                enroll_code="52159",
            ),
        ]

        result = replace_quarter_catalog(
            self.connection,
            "20271",
            catalog,
            expected_total=3,
        )

        sync_run = self.connection.execute(
            """
            SELECT status, expected_total, fetched_total
            FROM catalog_sync_runs
            WHERE id = ?
            """,
            (result["sync_run_id"],),
        ).fetchone()

        self.assertEqual("completed", sync_run["status"])
        self.assertEqual(3, sync_run["expected_total"])
        self.assertEqual(2, sync_run["fetched_total"])
        self.assertEqual(2, result["courses"])

    def test_expected_total_cannot_be_less_than_fetched_total(self):
        catalog = [
            make_course(),
            make_course(
                course_id="ANTH 1",
                title="INTRO HEALTH",
                department_code="ANTH",
                enroll_code="52159",
            ),
        ]

        with self.assertRaisesRegex(
            ValueError,
            "more courses than advertised",
        ):
            replace_quarter_catalog(
                self.connection,
                "20271",
                catalog,
                expected_total=1,
            )

        self.assertEqual(0, get_catalog_counts(self.connection, "20271")["courses"])

    def test_replacing_quarter_preserves_another_quarter(self):
        winter_2027 = [make_course()]
        spring_2027 = [
            make_course(
                course_id="MATH 4A",
                quarter="20272",
                title="LINEAR ALGEBRA",
                department_code="MATH",
                enroll_code="30106",
            )
        ]

        replace_quarter_catalog(self.connection, "20271", winter_2027, 1)
        replace_quarter_catalog(self.connection, "20272", spring_2027, 1)
        replace_quarter_catalog(
            self.connection,
            "20271",
            [make_course(title="UPDATED PROBLEM SOLVING")],
            1,
        )

        spring_result = get_course_sections(
            self.connection,
            "20272",
            "MATH 4A",
        )
        self.assertIsNotNone(spring_result)
        self.assertEqual("LINEAR ALGEBRA", spring_result["course"]["title"])
        self.assertEqual(1, get_catalog_counts(self.connection, "20272")["courses"])

    def test_failed_replacement_rolls_back_and_records_failure(self):
        replace_quarter_catalog(
            self.connection,
            "20271",
            [make_course(title="ORIGINAL TITLE")],
            1,
        )
        duplicate_ge = [
            {"ge_code": "C", "ge_college": "L&S"},
            {"ge_code": "C", "ge_college": "L&S"},
        ]
        invalid_replacement = [
            make_course(
                title="SHOULD NOT SURVIVE",
                general_education=duplicate_ge,
            )
        ]

        with self.assertRaises(sqlite3.IntegrityError):
            replace_quarter_catalog(
                self.connection,
                "20271",
                invalid_replacement,
                1,
            )

        result = get_course_sections(self.connection, "20271", "CMPSC 16")
        self.assertEqual("ORIGINAL TITLE", result["course"]["title"])
        self.assertEqual(1, get_catalog_counts(self.connection, "20271")["courses"])

        sync_runs = self.connection.execute(
            """
            SELECT status, expected_total, fetched_total, finished_at
            FROM catalog_sync_runs
            WHERE quarter = '20271'
            ORDER BY id
            """
        ).fetchall()
        self.assertEqual(
            ["completed", "failed"],
            [row["status"] for row in sync_runs],
        )
        self.assertEqual(1, sync_runs[1]["expected_total"])
        self.assertEqual(1, sync_runs[1]["fetched_total"])
        self.assertIn("T", sync_runs[1]["finished_at"])


if __name__ == "__main__":
    unittest.main()
