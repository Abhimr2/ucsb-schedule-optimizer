from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path


DEFAULT_QUARTER = "20271"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "instance" / "catalog.db"
SAMPLE_LIMIT = 5
PATTERN_LIMIT = 50
MISSING_VALUE = "__missing__"
PRIMARY_INSTRUCTION_TYPES = (
    "activity",
    "clinical",
    "fieldwork",
    "independent_study",
    "lecture",
    "practicum",
    "seminar",
    "studio",
)
_QUARTER_PATTERN = re.compile(r"\d{5}\Z")


def audit_catalog(connection, quarter):
    quarter = _validate_quarter(quarter)
    primary_placeholders = ", ".join("?" for _ in PRIMARY_INSTRUCTION_TYPES)
    primary_parameters = (quarter, *PRIMARY_INSTRUCTION_TYPES)

    row_counts = {
        "courses": _scalar(
            connection,
            "SELECT COUNT(*) FROM courses WHERE quarter = ?",
            (quarter,),
        ),
        "sections": _scalar(
            connection,
            "SELECT COUNT(*) FROM sections WHERE quarter = ?",
            (quarter,),
        ),
        "meetings": _scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM meetings AS meeting
            JOIN sections AS section ON section.id = meeting.section_id
            WHERE section.quarter = ?
            """,
            (quarter,),
        ),
        "meeting_days": _scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM meeting_days AS meeting_day
            JOIN meetings AS meeting ON meeting.id = meeting_day.meeting_id
            JOIN sections AS section ON section.id = meeting.section_id
            WHERE section.quarter = ?
            """,
            (quarter,),
        ),
        "instructors": _scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM section_instructors AS instructor
            JOIN sections AS section ON section.id = instructor.section_id
            WHERE section.quarter = ?
            """,
            (quarter,),
        ),
        "general_education": _scalar(
            connection,
            "SELECT COUNT(*) FROM course_general_education WHERE quarter = ?",
            (quarter,),
        ),
        "concurrent_courses": _scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM section_concurrent_courses AS concurrent_course
            JOIN sections AS section ON section.id = concurrent_course.section_id
            WHERE section.quarter = ?
            """,
            (quarter,),
        ),
    }

    instruction_type_counts = _grouped_counts(
        connection,
        """
        SELECT
            COALESCE(NULLIF(TRIM(instruction_type), ''), ?) AS value,
            COUNT(*) AS count
        FROM sections
        WHERE quarter = ?
        GROUP BY value
        ORDER BY value
        """,
        (MISSING_VALUE, quarter),
    )
    secondary_status_counts = _grouped_counts(
        connection,
        """
        SELECT
            COALESCE(NULLIF(TRIM(secondary_status), ''), ?) AS value,
            COUNT(*) AS count
        FROM sections
        WHERE quarter = ?
        GROUP BY value
        ORDER BY value
        """,
        (MISSING_VALUE, quarter),
    )

    status_counts = {
        "closed_sections": _scalar(
            connection,
            "SELECT COUNT(*) FROM sections WHERE quarter = ? AND class_closed = 1",
            (quarter,),
        ),
        "cancelled_sections": _scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM sections
            WHERE quarter = ? AND course_cancelled = 1
            """,
            (quarter,),
        ),
        "tba_meetings": _scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM meetings AS meeting
            JOIN sections AS section ON section.id = meeting.section_id
            WHERE section.quarter = ?
              AND (
                  meeting.is_tba = 1
                  OR meeting.start_minutes IS NULL
                  OR meeting.end_minutes IS NULL
              )
            """,
            (quarter,),
        ),
        "sections_with_tba_meetings": _scalar(
            connection,
            """
            SELECT COUNT(DISTINCT section.id)
            FROM sections AS section
            JOIN meetings AS meeting ON meeting.section_id = section.id
            WHERE section.quarter = ?
              AND (
                  meeting.is_tba = 1
                  OR meeting.start_minutes IS NULL
                  OR meeting.end_minutes IS NULL
              )
            """,
            (quarter,),
        ),
        "sections_without_meetings": _scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM sections AS section
            WHERE section.quarter = ?
              AND NOT EXISTS (
                  SELECT 1 FROM meetings WHERE meetings.section_id = section.id
              )
            """,
            (quarter,),
        ),
        "online_courses": _scalar(
            connection,
            "SELECT COUNT(*) FROM courses WHERE quarter = ? AND online = 1",
            (quarter,),
        ),
        "online_sections": _scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM sections AS section
            JOIN courses AS course
              ON course.quarter = section.quarter
             AND course.course_id = section.course_id
            WHERE section.quarter = ? AND course.online = 1
            """,
            (quarter,),
        ),
    }

    structure_counts = {
        "multi_meeting_sections": _count_grouped_sections(
            connection,
            quarter,
            child_table="meetings",
        ),
        "multi_instructor_sections": _count_grouped_sections(
            connection,
            quarter,
            child_table="section_instructors",
        ),
        "sections_missing_enroll_codes": _scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM sections
            WHERE quarter = ?
              AND (enroll_code IS NULL OR TRIM(enroll_code) = '')
            """,
            (quarter,),
        ),
        "sections_missing_times": _scalar(
            connection,
            """
            SELECT COUNT(*)
            FROM sections AS section
            WHERE section.quarter = ?
              AND (
                  NOT EXISTS (
                      SELECT 1
                      FROM meetings
                      WHERE meetings.section_id = section.id
                  )
                  OR EXISTS (
                      SELECT 1
                      FROM meetings
                      WHERE meetings.section_id = section.id
                        AND (
                            meetings.start_minutes IS NULL
                            OR meetings.end_minutes IS NULL
                        )
                  )
              )
            """,
            (quarter,),
        ),
        "courses_without_primary_instruction": _scalar(
            connection,
            f"""
            SELECT COUNT(*)
            FROM courses AS course
            WHERE course.quarter = ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM sections AS section
                  WHERE section.quarter = course.quarter
                    AND section.course_id = course.course_id
                    AND section.instruction_type IN ({primary_placeholders})
              )
            """,
            primary_parameters,
        ),
        "courses_with_multiple_lecture_sections": _count_courses_with_sections(
            connection,
            quarter,
            "section.instruction_type = 'lecture'",
        ),
        "courses_with_multiple_primary_sections": _count_courses_with_sections(
            connection,
            quarter,
            f"section.instruction_type IN ({primary_placeholders})",
            PRIMARY_INSTRUCTION_TYPES,
        ),
    }

    samples = {
        "closed_sections": _sample_course_ids(
            connection,
            "section.class_closed = 1",
            quarter,
        ),
        "cancelled_sections": _sample_course_ids(
            connection,
            "section.course_cancelled = 1",
            quarter,
        ),
        "tba_sections": _sample_course_ids(
            connection,
            """
            EXISTS (
                SELECT 1
                FROM meetings AS meeting
                WHERE meeting.section_id = section.id
                  AND (
                      meeting.is_tba = 1
                      OR meeting.start_minutes IS NULL
                      OR meeting.end_minutes IS NULL
                  )
            )
            """,
            quarter,
        ),
        "multi_meeting_sections": _sample_grouped_child_courses(
            connection,
            quarter,
            "meetings",
        ),
        "multi_instructor_sections": _sample_grouped_child_courses(
            connection,
            quarter,
            "section_instructors",
        ),
        "sections_missing_enroll_codes": _sample_course_ids(
            connection,
            "section.enroll_code IS NULL OR TRIM(section.enroll_code) = ''",
            quarter,
        ),
        "sections_missing_times": _sample_course_ids(
            connection,
            """
            NOT EXISTS (
                SELECT 1 FROM meetings WHERE meetings.section_id = section.id
            )
            OR EXISTS (
                SELECT 1
                FROM meetings
                WHERE meetings.section_id = section.id
                  AND (
                      meetings.start_minutes IS NULL
                      OR meetings.end_minutes IS NULL
                  )
            )
            """,
            quarter,
        ),
        "online_courses": _sample_direct_course_ids(
            connection,
            "online = 1",
            quarter,
        ),
        "courses_without_primary_instruction": _sample_courses_without_primary(
            connection,
            quarter,
            primary_placeholders,
        ),
        "courses_with_multiple_lecture_sections": _sample_courses_with_sections(
            connection,
            quarter,
            "section.instruction_type = 'lecture'",
        ),
        "courses_with_multiple_primary_sections": _sample_courses_with_sections(
            connection,
            quarter,
            f"section.instruction_type IN ({primary_placeholders})",
            PRIMARY_INSTRUCTION_TYPES,
        ),
    }

    return {
        "quarter": quarter,
        "row_counts": row_counts,
        "instruction_type_counts": instruction_type_counts,
        "secondary_status_counts": secondary_status_counts,
        "status_counts": status_counts,
        "structure_counts": structure_counts,
        "section_number_patterns": _section_number_patterns(connection, quarter),
        "samples": samples,
        "sample_limit": SAMPLE_LIMIT,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Audit a normalized UCSB catalog without modifying it."
    )
    parser.add_argument(
        "--quarter",
        default=DEFAULT_QUARTER,
        help=f"five-digit UCSB quarter code (default: {DEFAULT_QUARTER})",
    )
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE_PATH),
        help=f"existing catalog database (default: {DEFAULT_DATABASE_PATH})",
    )
    arguments = parser.parse_args(argv)
    connection = None

    try:
        quarter = _validate_quarter(arguments.quarter)
        database_path = Path(arguments.database).expanduser().resolve(strict=False)
        if not database_path.is_file():
            raise ValueError(f"catalog database does not exist: {database_path}")

        connection = sqlite3.connect(
            f"{database_path.as_uri()}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        report = audit_catalog(connection, quarter)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except (OSError, sqlite3.Error, TypeError, ValueError) as error:
        print(f"Catalog audit failed: {error}", file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()


def _scalar(connection, query, parameters=()):
    return int(connection.execute(query, parameters).fetchone()[0])


def _grouped_counts(connection, query, parameters=()):
    return {
        str(row[0]): int(row[1])
        for row in connection.execute(query, parameters).fetchall()
    }


def _count_grouped_sections(connection, quarter, child_table):
    if child_table not in {"meetings", "section_instructors"}:
        raise ValueError("unsupported child table")
    return _scalar(
        connection,
        f"""
        SELECT COUNT(*)
        FROM (
            SELECT section.id
            FROM sections AS section
            JOIN {child_table} AS child ON child.section_id = section.id
            WHERE section.quarter = ?
            GROUP BY section.id
            HAVING COUNT(*) > 1
        )
        """,
        (quarter,),
    )


def _count_courses_with_sections(
    connection,
    quarter,
    section_condition,
    extra_parameters=(),
):
    return _scalar(
        connection,
        f"""
        SELECT COUNT(*)
        FROM (
            SELECT section.course_id
            FROM sections AS section
            WHERE section.quarter = ? AND ({section_condition})
            GROUP BY section.course_id
            HAVING COUNT(*) > 1
        )
        """,
        (quarter, *extra_parameters),
    )


def _sample_course_ids(connection, condition, quarter):
    rows = connection.execute(
        f"""
        SELECT DISTINCT section.course_id
        FROM sections AS section
        WHERE section.quarter = ? AND ({condition})
        ORDER BY section.course_id COLLATE NOCASE
        LIMIT ?
        """,
        (quarter, SAMPLE_LIMIT),
    ).fetchall()
    return [row[0] for row in rows]


def _sample_direct_course_ids(connection, condition, quarter):
    rows = connection.execute(
        f"""
        SELECT course_id
        FROM courses
        WHERE quarter = ? AND ({condition})
        ORDER BY course_id COLLATE NOCASE
        LIMIT ?
        """,
        (quarter, SAMPLE_LIMIT),
    ).fetchall()
    return [row[0] for row in rows]


def _sample_grouped_child_courses(connection, quarter, child_table):
    if child_table not in {"meetings", "section_instructors"}:
        raise ValueError("unsupported child table")
    rows = connection.execute(
        f"""
        SELECT section.course_id
        FROM sections AS section
        JOIN {child_table} AS child ON child.section_id = section.id
        WHERE section.quarter = ?
        GROUP BY section.id, section.course_id
        HAVING COUNT(*) > 1
        ORDER BY section.course_id COLLATE NOCASE
        LIMIT ?
        """,
        (quarter, SAMPLE_LIMIT),
    ).fetchall()
    return _unique_values(row[0] for row in rows)


def _sample_courses_without_primary(connection, quarter, placeholders):
    rows = connection.execute(
        f"""
        SELECT course.course_id
        FROM courses AS course
        WHERE course.quarter = ?
          AND NOT EXISTS (
              SELECT 1
              FROM sections AS section
              WHERE section.quarter = course.quarter
                AND section.course_id = course.course_id
                AND section.instruction_type IN ({placeholders})
          )
        ORDER BY course.course_id COLLATE NOCASE
        LIMIT ?
        """,
        (quarter, *PRIMARY_INSTRUCTION_TYPES, SAMPLE_LIMIT),
    ).fetchall()
    return [row[0] for row in rows]


def _sample_courses_with_sections(
    connection,
    quarter,
    section_condition,
    extra_parameters=(),
):
    rows = connection.execute(
        f"""
        SELECT section.course_id
        FROM sections AS section
        WHERE section.quarter = ? AND ({section_condition})
        GROUP BY section.course_id
        HAVING COUNT(*) > 1
        ORDER BY section.course_id COLLATE NOCASE
        LIMIT ?
        """,
        (quarter, *extra_parameters, SAMPLE_LIMIT),
    ).fetchall()
    return [row[0] for row in rows]


def _section_number_patterns(connection, quarter):
    rows = connection.execute(
        """
        SELECT section_number, instruction_type
        FROM sections
        WHERE quarter = ?
        ORDER BY course_id, source_order
        """,
        (quarter,),
    ).fetchall()

    missing = 0
    numeric = 0
    non_numeric = 0
    length_counts = Counter()
    prefix_counts = Counter()
    suffix_counts = Counter()
    pair_counts = Counter()
    primary_suffix_counts = Counter()
    secondary_suffix_counts = Counter()

    for raw_number, instruction_type in rows:
        number = "" if raw_number is None else str(raw_number).strip()
        if not number:
            missing += 1
            continue

        length_counts[str(len(number))] += 1
        if not number.isdigit():
            non_numeric += 1
            continue

        numeric += 1
        prefix = number[:-2] or "<none>"
        suffix = number[-2:]
        prefix_counts[prefix] += 1
        suffix_counts[suffix] += 1
        pair_counts[f"{prefix}:{suffix}"] += 1
        if instruction_type in PRIMARY_INSTRUCTION_TYPES:
            primary_suffix_counts[suffix] += 1
        else:
            secondary_suffix_counts[suffix] += 1

    return {
        "missing": missing,
        "numeric": numeric,
        "non_numeric": non_numeric,
        "length_counts": _ordered_counts(length_counts),
        "prefix_counts": _ordered_counts(prefix_counts),
        "suffix_counts": _ordered_counts(suffix_counts),
        "prefix_suffix_counts": _ordered_counts(pair_counts, PATTERN_LIMIT),
        "primary_suffix_counts": _ordered_counts(primary_suffix_counts),
        "secondary_suffix_counts": _ordered_counts(secondary_suffix_counts),
    }


def _ordered_counts(counter, limit=None):
    ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    if limit is not None and len(ordered) > limit:
        kept = ordered[:limit]
        kept.append(("__other__", sum(value for _, value in ordered[limit:])))
        ordered = kept
    return {key: value for key, value in ordered}


def _unique_values(values):
    unique = []
    for value in values:
        if value not in unique:
            unique.append(value)
        if len(unique) == SAMPLE_LIMIT:
            break
    return unique


def _validate_quarter(quarter):
    if not isinstance(quarter, str) or _QUARTER_PATTERN.fullmatch(quarter) is None:
        raise ValueError("quarter must be exactly five digits, such as 20271")
    return quarter


if __name__ == "__main__":
    raise SystemExit(main())
