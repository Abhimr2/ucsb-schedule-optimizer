import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Sequence


COURSE_FIELDS = {
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
}

SECTION_FIELDS = {
    "enroll_code",
    "section_number",
    "session",
    "instruction_type_raw",
    "instruction_type",
    "class_closed",
    "course_cancelled",
    "grading_option_code",
    "enrolled_total",
    "max_enroll",
    "secondary_status",
    "department_approval_required",
    "instructor_approval_required",
    "restriction_level",
    "restriction_major",
    "restriction_major_pass",
    "restriction_minor",
    "restriction_minor_pass",
    "source_order",
    "source_json",
    "meetings",
    "instructors",
    "concurrent_courses",
}

MEETING_FIELDS = {
    "ordinal",
    "days",
    "days_raw",
    "begin_time",
    "end_time",
    "start_minutes",
    "end_minutes",
    "building",
    "room",
    "room_capacity",
    "is_tba",
}

INSTRUCTOR_FIELDS = {"ordinal", "name", "function_code"}
CONCURRENT_COURSE_FIELDS = {"ordinal", "value_json"}
GENERAL_EDUCATION_FIELDS = {"ge_code", "ge_college"}


def replace_quarter_catalog(
    connection: sqlite3.Connection,
    quarter: str,
    normalized_courses: Sequence[Mapping[str, Any]],
    expected_total: int,
) -> Dict[str, int]:
    started_at = _utc_now()
    fetched_total = (
        len(normalized_courses)
        if isinstance(normalized_courses, (list, tuple))
        else 0
    )

    try:
        _validate_catalog(quarter, normalized_courses, expected_total)
    except Exception as error:
        _record_failed_sync(
            connection,
            quarter,
            started_at,
            expected_total,
            fetched_total,
            error,
        )
        raise

    savepoint = "replace_quarter_catalog"
    had_transaction = connection.in_transaction

    if had_transaction:
        connection.execute(f"SAVEPOINT {savepoint}")
    else:
        connection.execute("BEGIN IMMEDIATE")

    try:
        connection.execute(
            "DELETE FROM courses WHERE quarter = ?",
            (quarter,),
        )

        for normalized_course in normalized_courses:
            _insert_course(connection, normalized_course)

        finished_at = _utc_now()
        sync_cursor = connection.execute(
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
            (
                quarter,
                started_at,
                finished_at,
                expected_total,
                fetched_total,
            ),
        )

        if had_transaction:
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        else:
            connection.commit()
    except Exception as error:
        if had_transaction:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        else:
            connection.rollback()

        _record_failed_sync(
            connection,
            quarter,
            started_at,
            expected_total,
            fetched_total,
            error,
        )
        raise

    result = get_catalog_counts(connection, quarter)
    result["sync_run_id"] = sync_cursor.lastrowid
    return result


def get_catalog_counts(
    connection: sqlite3.Connection,
    quarter: str,
) -> Dict[str, int]:
    row = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM courses WHERE quarter = :quarter)
                AS courses,
            (SELECT COUNT(*) FROM sections WHERE quarter = :quarter)
                AS sections,
            (
                SELECT COUNT(*)
                FROM meetings AS meeting
                JOIN sections AS section ON section.id = meeting.section_id
                WHERE section.quarter = :quarter
            ) AS meetings,
            (
                SELECT COUNT(*)
                FROM section_instructors AS instructor
                JOIN sections AS section ON section.id = instructor.section_id
                WHERE section.quarter = :quarter
            ) AS instructors,
            (
                SELECT COUNT(*)
                FROM course_general_education
                WHERE quarter = :quarter
            ) AS general_education
        """,
        {"quarter": quarter},
    ).fetchone()

    return dict(row)


def search_courses(
    connection: sqlite3.Connection,
    quarter: str,
    query: str = "",
    limit: int = 25,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")

    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be a non-negative integer")

    normalized_query = query.strip()
    pattern = f"%{_escape_like(normalized_query)}%"

    rows = connection.execute(
        """
        SELECT
            course.quarter,
            course.course_id,
            course.raw_course_id,
            course.title,
            course.subject_area,
            course.department_code,
            course.college,
            course.level_code,
            course.units_fixed,
            course.units_variable_low,
            course.units_variable_high,
            course.online,
            COUNT(section.id) AS section_count
        FROM courses AS course
        LEFT JOIN sections AS section
            ON section.quarter = course.quarter
            AND section.course_id = course.course_id
        WHERE course.quarter = ?
          AND (
              ? = ''
              OR course.course_id LIKE ? ESCAPE '\\' COLLATE NOCASE
              OR course.title LIKE ? ESCAPE '\\' COLLATE NOCASE
              OR COALESCE(course.subject_area, '')
                    LIKE ? ESCAPE '\\' COLLATE NOCASE
              OR COALESCE(course.department_code, '')
                    LIKE ? ESCAPE '\\' COLLATE NOCASE
          )
        GROUP BY course.quarter, course.course_id
        ORDER BY course.course_id COLLATE NOCASE
        LIMIT ? OFFSET ?
        """,
        (
            quarter,
            normalized_query,
            pattern,
            pattern,
            pattern,
            pattern,
            limit,
            offset,
        ),
    ).fetchall()

    return [dict(row) for row in rows]


def get_course_sections(
    connection: sqlite3.Connection,
    quarter: str,
    course_id: str,
):
    course_row = connection.execute(
        """
        SELECT *
        FROM courses
        WHERE quarter = ? AND course_id = ?
        """,
        (quarter, course_id),
    ).fetchone()

    if course_row is None:
        return None

    general_education = connection.execute(
        """
        SELECT ge_code, ge_college
        FROM course_general_education
        WHERE quarter = ? AND course_id = ?
        ORDER BY ge_code, ge_college
        """,
        (quarter, course_id),
    ).fetchall()

    section_rows = connection.execute(
        """
        SELECT *
        FROM sections
        WHERE quarter = ? AND course_id = ?
        ORDER BY source_order, enroll_code
        """,
        (quarter, course_id),
    ).fetchall()

    sections = []

    for section_row in section_rows:
        section = dict(section_row)
        section_id = section["id"]
        meeting_rows = connection.execute(
            """
            SELECT *
            FROM meetings
            WHERE section_id = ?
            ORDER BY ordinal
            """,
            (section_id,),
        ).fetchall()

        meetings = []

        for meeting_row in meeting_rows:
            meeting = dict(meeting_row)
            day_rows = connection.execute(
                """
                SELECT day
                FROM meeting_days
                WHERE meeting_id = ?
                ORDER BY ordinal
                """,
                (meeting["id"],),
            ).fetchall()
            meeting["days"] = [day_row["day"] for day_row in day_rows]
            meetings.append(meeting)

        instructor_rows = connection.execute(
            """
            SELECT ordinal, name, function_code
            FROM section_instructors
            WHERE section_id = ?
            ORDER BY ordinal
            """,
            (section_id,),
        ).fetchall()

        concurrent_rows = connection.execute(
            """
            SELECT ordinal, value_json
            FROM section_concurrent_courses
            WHERE section_id = ?
            ORDER BY ordinal
            """,
            (section_id,),
        ).fetchall()

        section["meetings"] = meetings
        section["instructors"] = [dict(row) for row in instructor_rows]
        section["concurrent_courses"] = [
            dict(row) for row in concurrent_rows
        ]
        sections.append(section)

    return {
        "course": dict(course_row),
        "general_education": [dict(row) for row in general_education],
        "sections": sections,
    }


def _insert_course(
    connection: sqlite3.Connection,
    normalized_course: Mapping[str, Any],
) -> None:
    course = normalized_course["course"]
    quarter = course["quarter"]
    course_id = course["course_id"]

    connection.execute(
        """
        INSERT INTO courses (
            quarter,
            course_id,
            raw_course_id,
            title,
            description,
            subject_area,
            department_code,
            college,
            level_code,
            contact_hours,
            units_fixed,
            units_variable_low,
            units_variable_high,
            grading_option,
            instruction_type,
            online,
            source_order,
            source_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            quarter,
            course_id,
            course["raw_course_id"],
            course["title"],
            course["description"],
            course["subject_area"],
            course["department_code"],
            course["college"],
            course["level_code"],
            course["contact_hours"],
            course["units_fixed"],
            course["units_variable_low"],
            course["units_variable_high"],
            course["grading_option"],
            course["instruction_type"],
            _boolean_value(course["online"], "course.online"),
            course["source_order"],
            _json_text(course["source_json"], "course.source_json"),
        ),
    )

    for general_education in normalized_course["general_education"]:
        connection.execute(
            """
            INSERT INTO course_general_education (
                quarter,
                course_id,
                ge_code,
                ge_college
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                quarter,
                course_id,
                general_education["ge_code"],
                general_education["ge_college"] or "",
            ),
        )

    for section in normalized_course["sections"]:
        _insert_section(connection, quarter, course_id, section)


def _insert_section(
    connection: sqlite3.Connection,
    quarter: str,
    course_id: str,
    section: Mapping[str, Any],
) -> None:
    cursor = connection.execute(
        """
        INSERT INTO sections (
            quarter,
            course_id,
            enroll_code,
            section_number,
            session,
            instruction_type_raw,
            instruction_type,
            class_closed,
            course_cancelled,
            grading_option_code,
            enrolled_total,
            max_enroll,
            secondary_status,
            department_approval_required,
            instructor_approval_required,
            restriction_level,
            restriction_major,
            restriction_major_pass,
            restriction_minor,
            restriction_minor_pass,
            source_order,
            source_json,
            parent_section_id,
            link_source
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            quarter,
            course_id,
            section["enroll_code"],
            section["section_number"],
            section["session"],
            section["instruction_type_raw"],
            section["instruction_type"],
            _boolean_value(section["class_closed"], "section.class_closed"),
            _boolean_value(
                section["course_cancelled"],
                "section.course_cancelled",
            ),
            section["grading_option_code"],
            section["enrolled_total"],
            section["max_enroll"],
            section["secondary_status"],
            _boolean_value(
                section["department_approval_required"],
                "section.department_approval_required",
            ),
            _boolean_value(
                section["instructor_approval_required"],
                "section.instructor_approval_required",
            ),
            section["restriction_level"],
            section["restriction_major"],
            section["restriction_major_pass"],
            section["restriction_minor"],
            section["restriction_minor_pass"],
            section["source_order"],
            _json_text(section["source_json"], "section.source_json"),
            section.get("parent_section_id"),
            section.get("link_source"),
        ),
    )
    section_id = cursor.lastrowid

    for meeting in section["meetings"]:
        meeting_cursor = connection.execute(
            """
            INSERT INTO meetings (
                section_id,
                ordinal,
                days_raw,
                begin_time,
                end_time,
                start_minutes,
                end_minutes,
                building,
                room,
                room_capacity,
                is_tba
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                section_id,
                meeting["ordinal"],
                meeting["days_raw"],
                meeting["begin_time"],
                meeting["end_time"],
                meeting["start_minutes"],
                meeting["end_minutes"],
                meeting["building"],
                meeting["room"],
                meeting["room_capacity"],
                _boolean_value(meeting["is_tba"], "meeting.is_tba"),
            ),
        )
        meeting_id = meeting_cursor.lastrowid

        for day_ordinal, day in enumerate(meeting["days"]):
            connection.execute(
                """
                INSERT INTO meeting_days (meeting_id, ordinal, day)
                VALUES (?, ?, ?)
                """,
                (meeting_id, day_ordinal, day),
            )

    for instructor in section["instructors"]:
        connection.execute(
            """
            INSERT INTO section_instructors (
                section_id,
                ordinal,
                name,
                function_code
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                section_id,
                instructor["ordinal"],
                instructor["name"],
                instructor["function_code"],
            ),
        )

    for concurrent_course in section["concurrent_courses"]:
        connection.execute(
            """
            INSERT INTO section_concurrent_courses (
                section_id,
                ordinal,
                value_json
            )
            VALUES (?, ?, ?)
            """,
            (
                section_id,
                concurrent_course["ordinal"],
                _json_text(
                    concurrent_course["value_json"],
                    "concurrent_course.value_json",
                ),
            ),
        )


def _validate_catalog(
    quarter: str,
    normalized_courses: Sequence[Mapping[str, Any]],
    expected_total: int,
) -> None:
    if not isinstance(quarter, str) or not quarter.strip():
        raise ValueError("quarter must be a non-empty string")

    if (
        isinstance(expected_total, bool)
        or not isinstance(expected_total, int)
        or expected_total < 0
    ):
        raise ValueError("expected_total must be a non-negative integer")

    if not isinstance(normalized_courses, (list, tuple)):
        raise TypeError("normalized_courses must be a list or tuple")

    if expected_total < len(normalized_courses):
        raise ValueError(
            "Catalog contains more courses than advertised: expected at most "
            f"{expected_total} courses but received {len(normalized_courses)}"
        )

    seen_course_ids = set()

    for course_index, normalized_course in enumerate(normalized_courses):
        label = f"normalized_courses[{course_index}]"
        _require_mapping(normalized_course, label)
        _require_fields(
            normalized_course,
            {"course", "general_education", "sections"},
            label,
        )

        course = normalized_course["course"]
        _require_mapping(course, f"{label}.course")
        _require_fields(course, COURSE_FIELDS, f"{label}.course")

        if course["quarter"] != quarter:
            raise ValueError(
                f"{label}.course.quarter must match requested quarter {quarter}"
            )

        _require_text(course["course_id"], f"{label}.course.course_id")
        _require_text(
            course["raw_course_id"],
            f"{label}.course.raw_course_id",
        )
        _require_text(course["title"], f"{label}.course.title")
        _require_integer(course["source_order"], f"{label}.course.source_order")
        _boolean_value(course["online"], f"{label}.course.online")
        _json_text(course["source_json"], f"{label}.course.source_json")

        if course["course_id"] in seen_course_ids:
            raise ValueError(
                f"Duplicate course_id for quarter {quarter}: "
                f"{course['course_id']}"
            )

        seen_course_ids.add(course["course_id"])

        general_education = normalized_course["general_education"]
        sections = normalized_course["sections"]
        _require_sequence(general_education, f"{label}.general_education")
        _require_sequence(sections, f"{label}.sections")

        for ge_index, ge_row in enumerate(general_education):
            ge_label = f"{label}.general_education[{ge_index}]"
            _require_mapping(ge_row, ge_label)
            _require_fields(ge_row, GENERAL_EDUCATION_FIELDS, ge_label)
            _require_text(ge_row["ge_code"], f"{ge_label}.ge_code")

            if ge_row["ge_college"] is not None:
                _require_text(ge_row["ge_college"], f"{ge_label}.ge_college")

        for section_index, section in enumerate(sections):
            _validate_section(section, f"{label}.sections[{section_index}]")


def _validate_section(section: Any, label: str) -> None:
    _require_mapping(section, label)
    _require_fields(section, SECTION_FIELDS, label)
    _require_text(section["enroll_code"], f"{label}.enroll_code")
    _require_text(section["section_number"], f"{label}.section_number")
    _require_integer(section["source_order"], f"{label}.source_order")
    _json_text(section["source_json"], f"{label}.source_json")

    for field in (
        "class_closed",
        "course_cancelled",
        "department_approval_required",
        "instructor_approval_required",
    ):
        _boolean_value(section[field], f"{label}.{field}")

    for field in ("restriction_major", "restriction_minor"):
        value = section[field]

        if value is not None and not isinstance(value, str):
            raise TypeError(f"{label}.{field} must be a string or None")

    _require_sequence(section["meetings"], f"{label}.meetings")
    _require_sequence(section["instructors"], f"{label}.instructors")
    _require_sequence(
        section["concurrent_courses"],
        f"{label}.concurrent_courses",
    )

    for meeting_index, meeting in enumerate(section["meetings"]):
        meeting_label = f"{label}.meetings[{meeting_index}]"
        _require_mapping(meeting, meeting_label)
        _require_fields(meeting, MEETING_FIELDS, meeting_label)
        _require_integer(meeting["ordinal"], f"{meeting_label}.ordinal")
        _require_sequence(meeting["days"], f"{meeting_label}.days")
        _boolean_value(meeting["is_tba"], f"{meeting_label}.is_tba")

        for day_index, day in enumerate(meeting["days"]):
            _require_text(day, f"{meeting_label}.days[{day_index}]")

    for instructor_index, instructor in enumerate(section["instructors"]):
        instructor_label = f"{label}.instructors[{instructor_index}]"
        _require_mapping(instructor, instructor_label)
        _require_fields(instructor, INSTRUCTOR_FIELDS, instructor_label)
        _require_integer(
            instructor["ordinal"],
            f"{instructor_label}.ordinal",
        )
        _require_text(instructor["name"], f"{instructor_label}.name")

    for concurrent_index, concurrent_course in enumerate(
        section["concurrent_courses"]
    ):
        concurrent_label = (
            f"{label}.concurrent_courses[{concurrent_index}]"
        )
        _require_mapping(concurrent_course, concurrent_label)
        _require_fields(
            concurrent_course,
            CONCURRENT_COURSE_FIELDS,
            concurrent_label,
        )
        _require_integer(
            concurrent_course["ordinal"],
            f"{concurrent_label}.ordinal",
        )
        _json_text(
            concurrent_course["value_json"],
            f"{concurrent_label}.value_json",
        )


def _record_failed_sync(
    connection: sqlite3.Connection,
    quarter: Any,
    started_at: str,
    expected_total: Any,
    fetched_total: int,
    error: Exception,
) -> None:
    had_transaction = connection.in_transaction

    try:
        if not had_transaction:
            connection.execute("BEGIN IMMEDIATE")

        connection.execute(
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
            (
                str(quarter),
                started_at,
                _utc_now(),
                _safe_expected_total(expected_total),
                fetched_total,
                str(error)[:2000],
            ),
        )

        if not had_transaction:
            connection.commit()
    except sqlite3.Error:
        if not had_transaction:
            connection.rollback()


def _safe_expected_total(value: Any):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _boolean_value(value: Any, label: str):
    if value is None:
        return None

    if isinstance(value, bool):
        return int(value)

    if isinstance(value, int) and value in (0, 1):
        return value

    raise TypeError(f"{label} must be True, False, 0, 1, or None")


def _json_text(value: Any, label: str) -> str:
    if isinstance(value, str):
        try:
            json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} must contain valid JSON") from error
        return value

    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be JSON serializable") from error


def _require_mapping(value: Any, label: str) -> None:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")


def _require_sequence(value: Any, label: str) -> None:
    if not isinstance(value, (list, tuple)):
        raise TypeError(f"{label} must be a list or tuple")


def _require_fields(
    mapping: Mapping[str, Any],
    fields,
    label: str,
) -> None:
    missing = sorted(fields - set(mapping))

    if missing:
        raise ValueError(f"{label} is missing fields: {', '.join(missing)}")


def _require_text(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise TypeError(f"{label} must be a non-empty string")


def _require_integer(value: Any, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
