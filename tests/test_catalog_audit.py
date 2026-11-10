import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from catalog_audit import audit_catalog, main  # noqa: E402
from catalog_database import connect_catalog, migrate_catalog  # noqa: E402
from catalog_normalizer import normalize_catalog  # noqa: E402
from catalog_repository import replace_quarter_catalog  # noqa: E402


def raw_section(
    enroll_code,
    section_number,
    instruction_type,
    meetings=None,
    instructors=None,
    *,
    closed=False,
    cancelled=False,
    secondary_status=None,
    concurrent_courses=None,
):
    return {
        "enrollCode": enroll_code,
        "section": section_number,
        "typeInstruction": instruction_type,
        "classClosed": closed,
        "courseCancelled": cancelled,
        "secondaryStatus": secondary_status,
        "timeLocations": list(meetings or []),
        "instructors": list(instructors or []),
        "concurrentCourses": list(concurrent_courses or []),
    }


def raw_course(
    course_id,
    title,
    sections,
    *,
    online=False,
    general_education=None,
):
    return {
        "quarter": "20271",
        "courseId": course_id,
        "title": title,
        "description": "Audit fixture",
        "subjectArea": course_id.split()[0],
        "deptCode": course_id.split()[0],
        "college": "L&S",
        "objLevelCode": "U",
        "unitsFixed": 4.0,
        "instructionType": "LEC",
        "onLineCourse": online,
        "generalEducation": list(general_education or []),
        "classSections": sections,
    }


def meeting(days, begin_time, end_time):
    return {
        "days": days,
        "beginTime": begin_time,
        "endTime": end_time,
        "building": "ILP",
        "room": "1203",
        "roomCapacity": 75,
    }


def instructor(name):
    return {
        "instructor": name,
        "functionCode": "Teaching and in charge",
    }


def audit_fixture():
    online_course = raw_course(
        "CMPSC     16  ",
        "PROBLEM SOLVING I",
        [
            raw_section(
                "08128",
                "0100",
                "LEC",
                meetings=[
                    meeting("M W", "09:30", "10:45"),
                    meeting("F", "09:00", "09:50"),
                ],
                instructors=[instructor("A ONE"), instructor("B TWO")],
                closed=True,
                secondary_status="R",
                concurrent_courses=[{"courseId": "ENGR       3  "}],
            ),
            raw_section(
                "08136",
                "0101",
                "DIS",
                meetings=[meeting("R", None, None)],
                cancelled=True,
                secondary_status="R",
            ),
        ],
        online=True,
        general_education=[{"geCode": "C", "geCollege": "L&S"}],
    )
    lab_only_course = raw_course(
        "CHEM       1A  ",
        "GENERAL CHEMISTRY",
        [
            raw_section(
                "10001",
                "0201",
                "LAB",
            )
        ],
    )
    multiple_lecture_course = raw_course(
        "MATH       4A  ",
        "LINEAR ALGEBRA",
        [
            raw_section(
                "30001",
                "0300",
                "LEC",
                meetings=[meeting("T R", "08:00", "09:15")],
                instructors=[instructor("C THREE")],
            ),
            raw_section(
                "30002",
                "A1",
                "LEC",
                meetings=[meeting("M W", "13:00", "14:15")],
                instructors=[instructor("D FOUR")],
            ),
        ],
    )
    return [online_course, lab_only_course, multiple_lecture_course]


class TestCatalogAudit(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "catalog.db"
        self.connection = connect_catalog(self.database_path)
        migrate_catalog(self.connection)
        normalized = normalize_catalog(audit_fixture())
        replace_quarter_catalog(self.connection, "20271", normalized, 3)

        self.connection.execute(
            """
            UPDATE sections
            SET enroll_code = ''
            WHERE quarter = '20271' AND course_id = 'CHEM 1A'
            """
        )
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.temporary_directory.cleanup()

    def test_audit_reports_linkage_metrics_and_bounded_samples(self):
        report = audit_catalog(self.connection, "20271")

        self.assertEqual("20271", report["quarter"])
        self.assertEqual(
            {
                "courses": 3,
                "sections": 5,
                "meetings": 5,
                "meeting_days": 8,
                "instructors": 4,
                "general_education": 1,
                "concurrent_courses": 1,
            },
            report["row_counts"],
        )
        self.assertEqual(
            {"discussion": 1, "lab": 1, "lecture": 3},
            report["instruction_type_counts"],
        )
        self.assertEqual(
            {"R": 2, "__missing__": 3},
            report["secondary_status_counts"],
        )
        self.assertEqual(
            {
                "closed_sections": 1,
                "cancelled_sections": 1,
                "tba_meetings": 1,
                "sections_with_tba_meetings": 1,
                "sections_without_meetings": 1,
                "online_courses": 1,
                "online_sections": 2,
            },
            report["status_counts"],
        )
        self.assertEqual(
            {
                "multi_meeting_sections": 1,
                "multi_instructor_sections": 1,
                "sections_missing_enroll_codes": 1,
                "sections_missing_times": 2,
                "courses_without_primary_instruction": 1,
                "courses_with_multiple_lecture_sections": 1,
                "courses_with_multiple_primary_sections": 1,
            },
            report["structure_counts"],
        )
        self.assertEqual(["CHEM 1A"], report["samples"]["courses_without_primary_instruction"])
        self.assertEqual(["MATH 4A"], report["samples"]["courses_with_multiple_lecture_sections"])
        self.assertTrue(
            all(
                len(course_ids) <= report["sample_limit"]
                for course_ids in report["samples"].values()
            )
        )
        json.dumps(report)

    def test_section_number_patterns_separate_primary_and_secondary_suffixes(self):
        patterns = audit_catalog(self.connection, "20271")[
            "section_number_patterns"
        ]

        self.assertEqual(4, patterns["numeric"])
        self.assertEqual(1, patterns["non_numeric"])
        self.assertEqual(0, patterns["missing"])
        self.assertEqual({"4": 4, "2": 1}, patterns["length_counts"])
        self.assertEqual(2, patterns["suffix_counts"]["00"])
        self.assertEqual(2, patterns["suffix_counts"]["01"])
        self.assertEqual(2, patterns["primary_suffix_counts"]["00"])
        self.assertEqual(2, patterns["secondary_suffix_counts"]["01"])

    def test_audit_does_not_assign_parent_links_or_write_rows(self):
        parents_before = self.connection.execute(
            "SELECT COUNT(*) FROM sections WHERE parent_section_id IS NOT NULL"
        ).fetchone()[0]
        changes_before = self.connection.total_changes

        audit_catalog(self.connection, "20271")

        parents_after = self.connection.execute(
            "SELECT COUNT(*) FROM sections WHERE parent_section_id IS NOT NULL"
        ).fetchone()[0]
        self.assertEqual(parents_before, parents_after)
        self.assertEqual(changes_before, self.connection.total_changes)

    def test_invalid_quarter_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "five digits"):
            audit_catalog(self.connection, "winter-2027")


class TestCatalogAuditCLI(unittest.TestCase):
    def test_cli_emits_readable_json_from_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "catalog.db"
            connection = connect_catalog(database_path)
            try:
                migrate_catalog(connection)
                normalized = normalize_catalog(audit_fixture())
                replace_quarter_catalog(connection, "20271", normalized, 3)
            finally:
                connection.close()

            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = main(
                    [
                        "--quarter",
                        "20271",
                        "--database",
                        str(database_path),
                    ]
                )

        self.assertEqual(0, exit_code)
        self.assertEqual("", stderr.getvalue())
        report = json.loads(stdout.getvalue())
        self.assertEqual(3, report["row_counts"]["courses"])

    def test_cli_rejects_missing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_path = Path(directory) / "missing.db"
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main(["--database", str(missing_path)])

        self.assertNotEqual(0, exit_code)
        self.assertIn("does not exist", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
