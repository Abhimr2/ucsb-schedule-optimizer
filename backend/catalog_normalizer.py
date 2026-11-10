from __future__ import annotations

import json
import re


_DAY_CODES = frozenset({"M", "T", "W", "R", "F", "S", "U"})
_TBA_VALUES = frozenset({"", "TBA", "ARR", "ARRANGED", "N/A", "NONE"})
_INSTRUCTION_TYPES = {
    "LEC": "lecture",
    "DIS": "discussion",
    "LAB": "lab",
    "REC": "recitation",
    "SEM": "seminar",
    "TUT": "tutorial",
    "ACT": "activity",
    "COL": "colloquium",
    "CLN": "clinical",
    "FLD": "fieldwork",
    "IND": "independent_study",
    "PRA": "practicum",
    "STU": "studio",
    "STD": "studio",
}


class CatalogNormalizationError(ValueError):
    pass


def normalize_course(raw_course, source_order=0):
    if not isinstance(raw_course, dict):
        raise TypeError("raw_course must be a dictionary")
    _validate_source_order(source_order)

    raw_course_id = raw_course.get("courseId")
    course = {
        "quarter": _clean_text(raw_course.get("quarter")),
        "course_id": _canonical_identifier(raw_course_id),
        "raw_course_id": _raw_text(raw_course_id),
        "title": _clean_text(raw_course.get("title")),
        "description": _clean_text(raw_course.get("description")),
        "subject_area": _clean_text(raw_course.get("subjectArea")),
        "department_code": _clean_text(raw_course.get("deptCode")),
        "college": _clean_text(raw_course.get("college")),
        "level_code": _clean_text(raw_course.get("objLevelCode")),
        "contact_hours": raw_course.get("contactHours"),
        "units_fixed": raw_course.get("unitsFixed"),
        "units_variable_low": raw_course.get("unitsVariableLow"),
        "units_variable_high": raw_course.get("unitsVariableHigh"),
        "grading_option": _clean_text(raw_course.get("gradingOption")),
        "instruction_type": _clean_text(raw_course.get("instructionType")),
        "online": _clean_value(raw_course.get("onLineCourse")),
        "source_order": source_order,
        "source_json": _json_text(raw_course),
    }

    general_education = []
    seen_general_education = set()
    for ordinal, raw_ge in enumerate(
        _list_field(raw_course, "generalEducation", context="course")
    ):
        if not isinstance(raw_ge, dict):
            raise CatalogNormalizationError(
                "course.generalEducation entries must be objects"
            )
        ge_code = _clean_text(raw_ge.get("geCode"))
        ge_college = _clean_text(raw_ge.get("geCollege"))
        identity = (ge_code, ge_college)
        if identity in seen_general_education:
            continue
        seen_general_education.add(identity)
        general_education.append(
            {
                "ordinal": ordinal,
                "ge_code": ge_code,
                "ge_college": ge_college,
                "source_json": _json_text(raw_ge),
            }
        )

    sections = []
    for section_order, raw_section in enumerate(
        _list_field(raw_course, "classSections", context="course")
    ):
        sections.append(_normalize_section(raw_section, section_order))

    return {
        "course": course,
        "general_education": general_education,
        "sections": sections,
    }


def normalize_catalog(raw_courses):
    if not isinstance(raw_courses, list):
        raise TypeError("raw_courses must be a list")
    return [
        normalize_course(raw_course, source_order=source_order)
        for source_order, raw_course in enumerate(raw_courses)
    ]


def _normalize_section(raw_section, source_order):
    if not isinstance(raw_section, dict):
        raise CatalogNormalizationError("course.classSections entries must be objects")

    instruction_type_raw = _clean_text(raw_section.get("typeInstruction"))
    meetings = [
        _normalize_meeting(raw_meeting, ordinal)
        for ordinal, raw_meeting in enumerate(
            _list_field(raw_section, "timeLocations", context="section")
        )
    ]
    instructors = [
        _normalize_instructor(raw_instructor, ordinal)
        for ordinal, raw_instructor in enumerate(
            _list_field(raw_section, "instructors", context="section")
        )
    ]
    concurrent_courses = [
        {
            "ordinal": ordinal,
            "value_json": _json_text(raw_value),
        }
        for ordinal, raw_value in enumerate(
            _list_field(raw_section, "concurrentCourses", context="section")
        )
    ]

    return {
        "enroll_code": _identifier_text(raw_section.get("enrollCode")),
        "section_number": _identifier_text(raw_section.get("section")),
        "session": _clean_text(raw_section.get("session")),
        "instruction_type_raw": instruction_type_raw,
        "instruction_type": _normalize_instruction_type(instruction_type_raw),
        "class_closed": _normalize_status_flag(
            raw_section.get("classClosed"),
            field_name="classClosed",
            true_codes={"Y"},
        ),
        "course_cancelled": _normalize_status_flag(
            raw_section.get("courseCancelled"),
            field_name="courseCancelled",
            true_codes={"C", "Y"},
        ),
        "grading_option_code": _clean_text(raw_section.get("gradingOptionCode")),
        "enrolled_total": raw_section.get("enrolledTotal"),
        "max_enroll": raw_section.get("maxEnroll"),
        "secondary_status": _clean_text(raw_section.get("secondaryStatus")),
        "department_approval_required": _clean_value(
            raw_section.get("departmentApprovalRequired")
        ),
        "instructor_approval_required": _clean_value(
            raw_section.get("instructorApprovalRequired")
        ),
        "restriction_level": _clean_value(raw_section.get("restrictionLevel")),
        "restriction_major": _clean_value(raw_section.get("restrictionMajor")),
        "restriction_major_pass": _clean_value(
            raw_section.get("restrictionMajorPass")
        ),
        "restriction_minor": _clean_value(raw_section.get("restrictionMinor")),
        "restriction_minor_pass": _clean_value(
            raw_section.get("restrictionMinorPass")
        ),
        "source_order": source_order,
        "source_json": _json_text(raw_section),
        "meetings": meetings,
        "instructors": instructors,
        "concurrent_courses": concurrent_courses,
    }


def _normalize_meeting(raw_meeting, ordinal):
    if not isinstance(raw_meeting, dict):
        raise CatalogNormalizationError("section.timeLocations entries must be objects")

    days_raw = _clean_text(raw_meeting.get("days"))
    begin_time = _clean_text(raw_meeting.get("beginTime"))
    end_time = _clean_text(raw_meeting.get("endTime"))
    days = _parse_days(days_raw)
    start_minutes = _parse_24_hour_time(begin_time)
    end_minutes = _parse_24_hour_time(end_time)

    return {
        "ordinal": ordinal,
        "days": days,
        "days_raw": days_raw,
        "begin_time": begin_time,
        "end_time": end_time,
        "start_minutes": start_minutes,
        "end_minutes": end_minutes,
        "building": _clean_text(raw_meeting.get("building")),
        "room": _clean_text(raw_meeting.get("room")),
        "room_capacity": raw_meeting.get("roomCapacity"),
        "is_tba": not days or start_minutes is None or end_minutes is None,
    }


def _normalize_instructor(raw_instructor, ordinal):
    if not isinstance(raw_instructor, dict):
        raise CatalogNormalizationError("section.instructors entries must be objects")
    return {
        "ordinal": ordinal,
        "name": _clean_text(raw_instructor.get("instructor")),
        "function_code": _clean_text(raw_instructor.get("functionCode")),
    }


def _normalize_instruction_type(value):
    if not value:
        return "other"
    return _INSTRUCTION_TYPES.get(value.upper(), "other")


def _normalize_status_flag(value, *, field_name, true_codes):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        code = value.strip().upper()
        if not code:
            return None
        if code in true_codes:
            return True
        if code == "N":
            return False

    expected_true_codes = ", ".join(sorted(true_codes))
    raise CatalogNormalizationError(
        f"Unknown {field_name} flag {value!r}; expected null/blank, a boolean, "
        f"0/1, N, or one of: {expected_true_codes}"
    )


def _parse_days(value):
    if value is None:
        return []
    upper = value.strip().upper()
    if upper in _TBA_VALUES or upper == "ONLINE":
        return []

    compact = re.sub(r"[\s,;/|-]+", "", upper)
    if not compact or any(character not in _DAY_CODES for character in compact):
        return []

    days = []
    for day in compact:
        if day not in days:
            days.append(day)
    return days


def _parse_24_hour_time(value):
    if value is None:
        return None
    upper = value.strip().upper()
    if upper in _TBA_VALUES:
        return None

    match = re.fullmatch(r"(\d{1,2}):(\d{2})", upper)
    if match is None:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def _list_field(container, field, *, context):
    value = container.get(field)
    if value is None:
        return []
    if not isinstance(value, list):
        raise CatalogNormalizationError(f"{context}.{field} must be a list or null")
    return value


def _clean_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _raw_text(value):
    if value is None:
        return None
    return str(value)


def _canonical_identifier(value):
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _identifier_text(value):
    if value is None:
        return None
    return str(value).strip()


def _clean_value(value):
    if isinstance(value, str):
        return value.strip() or None
    return value


def _json_text(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_source_order(source_order):
    if isinstance(source_order, bool) or not isinstance(source_order, int):
        raise TypeError("source_order must be a non-negative integer")
    if source_order < 0:
        raise ValueError("source_order must be a non-negative integer")
