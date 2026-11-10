import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from catalog_normalizer import (  # noqa: E402
    CatalogNormalizationError,
    normalize_catalog,
    normalize_course,
)


FIXTURE_PATH = ROOT / "tests" / "fixtures" / "ucsb_course_page.json"


def load_raw_course():
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return payload["classes"][0]


class TestNormalizeCourse(unittest.TestCase):
    def test_course_fields_are_trimmed_and_source_is_preserved(self):
        raw_course = load_raw_course()
        normalized = normalize_course(raw_course, source_order=7)
        course = normalized["course"]

        self.assertEqual(
            set(course),
            {
                "quarter",
                "course_id",
                "raw_course_id",
                "title",
                "description",
                "subject_area",
                "department_code",
                "college",
                "level_code",
                "contact_hours",
                "units_fixed",
                "units_variable_low",
                "units_variable_high",
                "grading_option",
                "instruction_type",
                "online",
                "source_order",
                "source_json",
            },
        )
        self.assertEqual(course["quarter"], "20271")
        self.assertEqual(course["course_id"], "CMPSC 16")
        self.assertEqual(course["raw_course_id"], "CMPSC     16  ")
        self.assertEqual(course["title"], "PROBLEM SOLVING I")
        self.assertEqual(course["description"], "Foundations of computational problem solving.")
        self.assertEqual(course["subject_area"], "CMPSC")
        self.assertEqual(course["department_code"], "CMPSC")
        self.assertEqual(course["college"], "ENGR")
        self.assertEqual(course["level_code"], "U")
        self.assertEqual(course["grading_option"], "L")
        self.assertEqual(course["instruction_type"], "LEC")
        self.assertFalse(course["online"])
        self.assertEqual(course["source_order"], 7)
        self.assertEqual(json.loads(course["source_json"]), raw_course)

    def test_general_education_is_normalized(self):
        ge_rows = normalize_course(load_raw_course())["general_education"]

        self.assertEqual(len(ge_rows), 1)
        self.assertEqual(ge_rows[0]["ordinal"], 0)
        self.assertEqual(ge_rows[0]["ge_code"], "C")
        self.assertEqual(ge_rows[0]["ge_college"], "L&S")
        self.assertEqual(
            json.loads(ge_rows[0]["source_json"]),
            {"geCode": "C  ", "geCollege": "L&S "},
        )

    def test_duplicate_general_education_entries_are_deduplicated(self):
        raw_course = load_raw_course()
        raw_course["generalEducation"].append(
            dict(raw_course["generalEducation"][0])
        )

        ge_rows = normalize_course(raw_course)["general_education"]

        self.assertEqual(len(ge_rows), 1)

    def test_lecture_meetings_instructors_and_concurrent_courses(self):
        lecture = normalize_course(load_raw_course())["sections"][0]

        self.assertEqual(lecture["enroll_code"], "08128")
        self.assertEqual(lecture["section_number"], "0100")
        self.assertEqual(lecture["instruction_type_raw"], "LEC")
        self.assertEqual(lecture["instruction_type"], "lecture")
        self.assertEqual(lecture["secondary_status"], "R")
        self.assertEqual(lecture["restriction_level"], "U")
        self.assertEqual(lecture["source_order"], 0)
        self.assertEqual(len(lecture["meetings"]), 2)
        self.assertEqual(len(lecture["instructors"]), 2)
        self.assertEqual(len(lecture["concurrent_courses"]), 2)

        meeting = lecture["meetings"][0]
        self.assertEqual(meeting["ordinal"], 0)
        self.assertEqual(meeting["days"], ["M", "W"])
        self.assertEqual(meeting["days_raw"], "M W")
        self.assertEqual(meeting["begin_time"], "09:30")
        self.assertEqual(meeting["end_time"], "10:45")
        self.assertEqual(meeting["start_minutes"], 570)
        self.assertEqual(meeting["end_minutes"], 645)
        self.assertEqual(meeting["building"], "ILP")
        self.assertEqual(meeting["room"], "1203")
        self.assertFalse(meeting["is_tba"])

        tba_meeting = lecture["meetings"][1]
        self.assertEqual(tba_meeting["days"], [])
        self.assertIsNone(tba_meeting["start_minutes"])
        self.assertIsNone(tba_meeting["end_minutes"])
        self.assertTrue(tba_meeting["is_tba"])

        self.assertEqual(
            lecture["instructors"][0],
            {
                "ordinal": 0,
                "name": "MAJEDI M",
                "function_code": "Teaching and in charge",
            },
        )
        self.assertEqual(lecture["instructors"][1]["name"], "DOE A")
        self.assertEqual(
            json.loads(lecture["concurrent_courses"][0]["value_json"]),
            {"courseId": "MATH       3A "},
        )
        self.assertEqual(
            json.loads(lecture["concurrent_courses"][1]["value_json"]),
            "ENGR 3  ",
        )

    def test_discussion_and_missing_times_are_preserved(self):
        discussion = normalize_course(load_raw_course())["sections"][1]
        meeting = discussion["meetings"][0]

        self.assertEqual(discussion["instruction_type_raw"], "DIS")
        self.assertEqual(discussion["instruction_type"], "discussion")
        self.assertEqual(discussion["session"], "1")
        self.assertEqual(discussion["restriction_major"], "CMPSC")
        self.assertEqual(discussion["restriction_major_pass"], "1")
        self.assertEqual(meeting["days"], ["R"])
        self.assertIsNone(meeting["begin_time"])
        self.assertIsNone(meeting["end_time"])
        self.assertIsNone(meeting["start_minutes"])
        self.assertIsNone(meeting["end_minutes"])
        self.assertTrue(meeting["is_tba"])

    def test_source_status_codes_are_normalized_and_preserved_in_source_json(self):
        raw_course = load_raw_course()
        section = raw_course["classSections"][0]
        section["classClosed"] = " Y "
        section["courseCancelled"] = " C "

        normalized_section = normalize_course(raw_course)["sections"][0]
        preserved_source = json.loads(normalized_section["source_json"])

        self.assertIs(normalized_section["class_closed"], True)
        self.assertIs(normalized_section["course_cancelled"], True)
        self.assertEqual(preserved_source["classClosed"], " Y ")
        self.assertEqual(preserved_source["courseCancelled"], " C ")

    def test_status_flags_accept_booleans_numbers_null_and_n(self):
        cases = [
            (True, False, True, False),
            (False, True, False, True),
            (1, 0, True, False),
            (0, 1, False, True),
            (None, None, None, None),
            (" N ", "N", False, False),
            ("   ", "", None, None),
        ]

        for class_closed, course_cancelled, expected_closed, expected_cancelled in cases:
            with self.subTest(
                class_closed=class_closed,
                course_cancelled=course_cancelled,
            ):
                raw_course = load_raw_course()
                section = raw_course["classSections"][0]
                section["classClosed"] = class_closed
                section["courseCancelled"] = course_cancelled

                normalized_section = normalize_course(raw_course)["sections"][0]

                self.assertIs(normalized_section["class_closed"], expected_closed)
                self.assertIs(
                    normalized_section["course_cancelled"], expected_cancelled
                )

    def test_unknown_nonempty_status_flags_raise_useful_errors(self):
        for field_name, value in (
            ("classClosed", "X"),
            ("courseCancelled", "Z "),
            ("classClosed", 2),
        ):
            with self.subTest(field_name=field_name, value=value):
                raw_course = load_raw_course()
                raw_course["classSections"][0][field_name] = value

                with self.assertRaisesRegex(
                    CatalogNormalizationError, f"Unknown {field_name} flag"
                ):
                    normalize_course(raw_course)

    def test_unknown_instruction_code_is_preserved_as_raw(self):
        raw_course = load_raw_course()
        section = raw_course["classSections"][0]
        section["typeInstruction"] = "XYZ "

        normalized_section = normalize_course(raw_course)["sections"][0]

        self.assertEqual(normalized_section["instruction_type_raw"], "XYZ")
        self.assertEqual(normalized_section["instruction_type"], "other")
        self.assertIn('"typeInstruction":"XYZ "', normalized_section["source_json"])

    def test_live_ucsb_instruction_codes_are_normalized(self):
        for raw_code, expected in (
            ("TUT", "tutorial"),
            ("STD", "studio"),
            ("COL", "colloquium"),
        ):
            with self.subTest(raw_code=raw_code):
                raw_course = load_raw_course()
                raw_course["classSections"][0]["typeInstruction"] = raw_code

                normalized_section = normalize_course(raw_course)["sections"][0]

                self.assertEqual(normalized_section["instruction_type"], expected)

    def test_numeric_section_identifiers_become_text(self):
        raw_course = load_raw_course()
        section = raw_course["classSections"][0]
        section["enrollCode"] = 8128
        section["section"] = 100

        normalized_section = normalize_course(raw_course)["sections"][0]

        self.assertEqual(normalized_section["enroll_code"], "8128")
        self.assertEqual(normalized_section["section_number"], "100")

    def test_missing_optional_collections_do_not_crash(self):
        raw_course = {
            "quarter": "20271",
            "courseId": "TEST      1 ",
            "generalEducation": None,
            "classSections": None,
        }

        normalized = normalize_course(raw_course)

        self.assertEqual(normalized["course"]["course_id"], "TEST 1")
        self.assertEqual(normalized["general_education"], [])
        self.assertEqual(normalized["sections"], [])

    def test_normalization_does_not_mutate_source(self):
        raw_course = load_raw_course()
        original = copy.deepcopy(raw_course)

        normalize_course(raw_course)

        self.assertEqual(raw_course, original)

    def test_invalid_collection_shape_has_useful_error(self):
        raw_course = load_raw_course()
        raw_course["classSections"] = {"not": "a list"}

        with self.assertRaisesRegex(
            CatalogNormalizationError, "course.classSections must be a list"
        ):
            normalize_course(raw_course)


class TestNormalizeCatalog(unittest.TestCase):
    def test_catalog_assigns_course_source_order(self):
        first = load_raw_course()
        second = copy.deepcopy(first)
        second["courseId"] = "MATH       4A "

        normalized = normalize_catalog([first, second])

        self.assertEqual(len(normalized), 2)
        self.assertEqual(normalized[0]["course"]["source_order"], 0)
        self.assertEqual(normalized[1]["course"]["source_order"], 1)
        self.assertEqual(normalized[1]["course"]["course_id"], "MATH 4A")

    def test_catalog_requires_a_list(self):
        with self.assertRaisesRegex(TypeError, "raw_courses must be a list"):
            normalize_catalog({"classes": []})


if __name__ == "__main__":
    unittest.main()
