import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from catalog_database import connect_catalog, migrate_catalog
from catalog_repository import replace_quarter_catalog
from course_loader import (
    UnresolvedSectionGroupError,
    block_from_meeting,
    build_course_options,
    load_course,
    load_courses,
    section_from_record,
)


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
        "room_capacity": 50,
        "is_tba": is_tba,
    }


def make_section(
    section_number="0100",
    enroll_code=None,
    instruction_type="lecture",
    secondary_status=None,
    meetings=None,
    instructors=None,
    session=None,
    closed=False,
    cancelled=False,
    enrolled_total=10,
    max_enroll=50,
    source_order=0,
):
    if enroll_code is None:
        enroll_code = f"E{section_number}"

    if meetings is None:
        meetings = [make_meeting()]

    if instructors is None:
        instructors = [
            {
                "ordinal": 0,
                "name": "ADA LOVELACE",
                "function_code": "Teaching and in charge",
            }
        ]

    return {
        "enroll_code": enroll_code,
        "section_number": section_number,
        "session": session,
        "instruction_type_raw": instruction_type.upper(),
        "instruction_type": instruction_type,
        "class_closed": closed,
        "course_cancelled": cancelled,
        "grading_option_code": None,
        "enrolled_total": enrolled_total,
        "max_enroll": max_enroll,
        "secondary_status": secondary_status,
        "department_approval_required": False,
        "instructor_approval_required": False,
        "restriction_level": None,
        "restriction_major": None,
        "restriction_major_pass": None,
        "restriction_minor": None,
        "restriction_minor_pass": None,
        "source_order": source_order,
        "source_json": {"section": section_number},
        "meetings": meetings,
        "instructors": instructors,
        "concurrent_courses": [],
    }


def make_catalog_course(
    course_id,
    title,
    enroll_code,
    units=4.0,
    closed=False,
):
    section = make_section(
        enroll_code=enroll_code,
        closed=closed,
    )

    return {
        "course": {
            "quarter": "20271",
            "course_id": course_id,
            "raw_course_id": course_id,
            "title": title,
            "description": f"Description for {course_id}",
            "subject_area": course_id.split()[0],
            "department_code": course_id.split()[0],
            "college": "L&S",
            "level_code": "U",
            "contact_hours": 30.0,
            "units_fixed": units,
            "units_variable_low": None,
            "units_variable_high": None,
            "grading_option": None,
            "instruction_type": "LEC",
            "online": False,
            "source_order": 0,
            "source_json": {"courseId": course_id},
        },
        "general_education": [],
        "sections": [section],
    }


class ConversionTests(unittest.TestCase):
    def test_block_from_meeting_preserves_time_location_and_tba(self):
        meeting = make_meeting(
            days=("T", "R"),
            start=None,
            end=None,
            building="ON",
            room="ASYNC",
            is_tba=True,
        )

        block = block_from_meeting(meeting, "lecture")

        self.assertEqual(("T", "R"), tuple(block.days))
        self.assertIsNone(block.start_time)
        self.assertIsNone(block.end_time)
        self.assertEqual("lecture", block.meeting_type)
        self.assertEqual("ON", block.building)
        self.assertEqual("ASYNC", block.room)
        self.assertTrue(block.is_tba)

    def test_section_preserves_all_meetings_and_instructors(self):
        record = make_section(
            meetings=[
                make_meeting(days=("M",), ordinal=0),
                make_meeting(
                    days=("F",),
                    start=780,
                    end=830,
                    building="PHELP",
                    room="3525",
                    ordinal=1,
                ),
            ],
            instructors=[
                {"ordinal": 0, "name": "FIRST INSTRUCTOR"},
                {"ordinal": 1, "name": "SECOND INSTRUCTOR"},
            ],
        )

        section = section_from_record(record)

        self.assertEqual(2, len(section.blocks))
        self.assertEqual("PHELP", section.blocks[1].building)
        self.assertEqual(
            ("FIRST INSTRUCTOR", "SECOND INSTRUCTOR"),
            tuple(section.instructors),
        )
        self.assertEqual(10, section.enrolled_total)
        self.assertEqual(50, section.max_enroll)

    def test_section_with_no_meetings_has_zero_blocks(self):
        section = section_from_record(make_section(meetings=[]))

        self.assertEqual((), tuple(section.blocks))


class OptionBuilderTests(unittest.TestCase):
    def test_lecture_only_section_is_standalone(self):
        options = build_course_options("PHIL 1", [make_section()])

        self.assertEqual(1, len(options))
        self.assertEqual(["0100"], [s.section_number for s in options[0].sections])

    def test_required_parent_creates_one_option_per_child(self):
        records = [
            make_section("0100", secondary_status="R"),
            make_section("0101", instruction_type="discussion"),
            make_section("0102", instruction_type="discussion"),
        ]

        options = build_course_options("CMPSC 16", records)

        self.assertEqual(2, len(options))
        self.assertEqual(
            [("0100", "0101"), ("0100", "0102")],
            [tuple(s.section_number for s in option.sections) for option in options],
        )

    def test_multiple_families_never_cross_pair(self):
        records = [
            make_section("0100", secondary_status="R"),
            make_section("0101", instruction_type="discussion"),
            make_section("0200", secondary_status="R"),
            make_section("0201", instruction_type="discussion"),
        ]

        options = build_course_options("MATH 8", records)

        self.assertEqual(
            [("0100", "0101"), ("0200", "0201")],
            [tuple(s.section_number for s in option.sections) for option in options],
        )

    def test_same_family_prefix_in_different_sessions_stays_separate(self):
        records = [
            make_section(
                "0100",
                enroll_code="A100",
                secondary_status="R",
                session="A",
            ),
            make_section(
                "0101",
                enroll_code="A101",
                instruction_type="discussion",
                session="A",
            ),
            make_section(
                "0100",
                enroll_code="B100",
                secondary_status="R",
                session="B",
            ),
            make_section(
                "0101",
                enroll_code="B101",
                instruction_type="discussion",
                session="B",
            ),
        ]

        options = build_course_options("TEST 2", records)

        self.assertEqual(
            [("A100", "A101"), ("B100", "B101")],
            [option.enroll_codes for option in options],
        )

    def test_mixed_standalone_and_required_families(self):
        records = [
            make_section("0100"),
            make_section("0200", secondary_status="R"),
            make_section("0201", instruction_type="other"),
            make_section("0202", instruction_type="other"),
        ]

        options = build_course_options("EEMB 2", records)

        self.assertEqual(
            [("0100",), ("0200", "0201"), ("0200", "0202")],
            [tuple(s.section_number for s in option.sections) for option in options],
        )

    def test_lab_parent_and_lab_children_use_status_not_type(self):
        records = [
            make_section(
                "0100",
                instruction_type="lab",
                secondary_status="R",
            ),
            make_section("0101", instruction_type="lab"),
            make_section("0102", instruction_type="lab"),
        ]

        options = build_course_options("MUS 4B", records)

        self.assertEqual(2, len(options))
        self.assertTrue(
            all(section.instruction_type == "lab" for section in options[0].sections)
        )

    def test_cancelled_parent_never_orphans_active_children(self):
        records = [
            make_section("0100", secondary_status="R", cancelled=True),
            make_section("0101", instruction_type="discussion"),
        ]

        self.assertEqual((), build_course_options("ANTH 1", records))

    def test_closed_parent_and_child_follow_include_closed_policy(self):
        parent_closed = [
            make_section("0100", secondary_status="R", closed=True),
            make_section("0101", instruction_type="discussion"),
        ]
        child_closed = [
            make_section("0100", secondary_status="R"),
            make_section("0101", instruction_type="discussion", closed=True),
            make_section("0102", instruction_type="discussion"),
        ]

        self.assertEqual((), build_course_options("ANTH 1", parent_closed))
        self.assertEqual(
            1,
            len(build_course_options("ANTH 1", parent_closed, include_closed=True)),
        )
        self.assertEqual(1, len(build_course_options("ANTH 2", child_closed)))
        self.assertEqual(
            2,
            len(build_course_options("ANTH 2", child_closed, include_closed=True)),
        )

    def test_cancelled_child_is_always_excluded(self):
        records = [
            make_section("0100", secondary_status="R"),
            make_section("0101", instruction_type="discussion", cancelled=True),
            make_section("0102", instruction_type="discussion"),
        ]

        options = build_course_options(
            "ANTH 1",
            records,
            include_closed=True,
        )

        self.assertEqual(
            [("0100", "0102")],
            [tuple(s.section_number for s in option.sections) for option in options],
        )

    def test_cancelled_unanchored_secondary_does_not_make_family_ambiguous(self):
        records = [
            make_section("0100"),
            make_section("0101", instruction_type="lab", cancelled=True),
        ]

        options = build_course_options("ARTHI 136W", records)

        self.assertEqual(1, len(options))
        self.assertEqual("0100", options[0].sections[0].section_number)

    def test_malformed_and_ambiguous_families_raise_with_context(self):
        cases = [
            [make_section("100")],
            [make_section("0101", instruction_type="discussion")],
            [
                make_section("0100", secondary_status="R"),
                make_section("0101", secondary_status="R"),
            ],
        ]

        for records in cases:
            with self.subTest(records=records):
                with self.assertRaises(UnresolvedSectionGroupError) as raised:
                    build_course_options("TEST 1", records)

                self.assertEqual("TEST 1", raised.exception.course_id)
                self.assertIn("TEST 1", str(raised.exception))

    def test_internally_conflicting_linked_option_is_retained(self):
        parent_meeting = make_meeting(days=("T",), start=840, end=915)
        child_meeting = make_meeting(days=("T",), start=900, end=950)
        records = [
            make_section(
                "0100",
                secondary_status="R",
                meetings=[parent_meeting],
            ),
            make_section(
                "0101",
                instruction_type="discussion",
                meetings=[child_meeting],
            ),
        ]

        options = build_course_options("MES 45", records)

        self.assertEqual(1, len(options))
        self.assertEqual(2, len(options[0].sections))


class RepositoryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "catalog.db"
        self.connection = connect_catalog(self.database_path)
        migrate_catalog(self.connection)
        replace_quarter_catalog(
            self.connection,
            "20271",
            [
                make_catalog_course("CMPSC 16", "PROBLEM SOLVING I", "08128"),
                make_catalog_course(
                    "MATH 4A",
                    "LINEAR ALGEBRA",
                    "30106",
                    units=5.0,
                    closed=True,
                ),
            ],
            expected_total=2,
        )

    def tearDown(self):
        self.connection.close()
        self.temporary_directory.cleanup()

    def test_load_course_maps_catalog_fields_and_closed_policy(self):
        course = load_course(self.connection, "20271", "CMPSC 16")
        hidden_closed = load_course(self.connection, "20271", "MATH 4A")
        included_closed = load_course(
            self.connection,
            "20271",
            "MATH 4A",
            include_closed=True,
        )

        self.assertEqual("PROBLEM SOLVING I", course.name)
        self.assertEqual("CMPSC 16", course.code)
        self.assertEqual(4.0, course.units)
        self.assertEqual("Description for CMPSC 16", course.description)
        self.assertEqual(1, len(course.options))
        self.assertEqual(0, len(hidden_closed.options))
        self.assertEqual(1, len(included_closed.options))

    def test_load_courses_preserves_requested_order(self):
        courses = load_courses(
            self.connection,
            "20271",
            ["MATH 4A", "CMPSC 16"],
            include_closed=True,
        )

        self.assertEqual(["MATH 4A", "CMPSC 16"], [course.code for course in courses])

    def test_load_course_raises_clear_lookup_error(self):
        with self.assertRaisesRegex(
            LookupError,
            "MISSING 1.*20271",
        ):
            load_course(self.connection, "20271", "MISSING 1")


if __name__ == "__main__":
    unittest.main()
