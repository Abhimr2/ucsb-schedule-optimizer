import re
from collections import defaultdict

from catalog_repository import get_course_sections
from model import Block, Course, CourseOption, Section


SECTION_NUMBER_PATTERN = re.compile(r"^\d{4}$")


class UnresolvedSectionGroupError(ValueError):
    def __init__(self, course_id, family, reason, session=None):
        self.course_id = course_id
        self.family = family
        self.session = session
        self.reason = reason

        session_label = session if session is not None else "default"
        super().__init__(
            f"Unable to resolve sections for {course_id!r} "
            f"(session={session_label!r}, family={family!r}): {reason}"
        )


def block_from_meeting(record, instruction_type):
    days = record.get("days") or ()

    if isinstance(days, str):
        days = days.split()

    return Block(
        tuple(days),
        record.get("start_minutes"),
        record.get("end_minutes"),
        instruction_type,
        building=record.get("building"),
        room=record.get("room"),
        is_tba=bool(record.get("is_tba")),
    )


def section_from_record(record):
    instruction_type = (
        record.get("instruction_type")
        or record.get("instruction_type_raw")
        or "other"
    )
    blocks = tuple(
        block_from_meeting(meeting, instruction_type)
        for meeting in record.get("meetings", ())
    )
    instructors = tuple(
        instructor["name"]
        for instructor in record.get("instructors", ())
        if instructor.get("name") is not None
    )

    return Section(
        record["enroll_code"],
        record["section_number"],
        instruction_type,
        blocks=blocks,
        session=record.get("session"),
        secondary_status=record.get("secondary_status"),
        instructors=instructors,
        closed=bool(record.get("class_closed")),
        cancelled=bool(record.get("course_cancelled")),
        enrolled_total=record.get("enrolled_total"),
        max_enroll=record.get("max_enroll"),
    )


def build_course_options(
    course_id,
    section_records,
    include_closed=False,
):
    grouped_records = defaultdict(list)

    for record in section_records:
        section_number = record.get("section_number")

        if not isinstance(section_number, str) or not SECTION_NUMBER_PATTERN.fullmatch(
            section_number
        ):
            family = (
                section_number[:2]
                if isinstance(section_number, str) and section_number
                else "invalid"
            )
            raise UnresolvedSectionGroupError(
                course_id,
                family,
                f"malformed section number {section_number!r}; expected four digits",
                record.get("session"),
            )

        family_key = (record.get("session"), section_number[:2])
        grouped_records[family_key].append((record, section_from_record(record)))

    options = []

    for (session, family), members in grouped_records.items():
        anchors = [
            member
            for member in members
            if _is_required_anchor(member[0].get("secondary_status"))
        ]

        if len(anchors) > 1:
            anchor_numbers = [record["section_number"] for record, _ in anchors]
            raise UnresolvedSectionGroupError(
                course_id,
                family,
                f"multiple required anchors: {anchor_numbers}",
                session,
            )

        if len(anchors) == 1:
            anchor_record, anchor = anchors[0]

            if not _is_eligible(anchor_record, include_closed):
                continue

            for child_record, child in members:
                if child_record is anchor_record:
                    continue

                if _is_eligible(child_record, include_closed):
                    options.append(CourseOption((anchor, child)))

            continue

        eligible_members = [
            member
            for member in members
            if _is_eligible(member[0], include_closed)
        ]
        unresolved_children = [
            record["section_number"]
            for record, _ in eligible_members
            if not record["section_number"].endswith("00")
        ]

        if unresolved_children:
            raise UnresolvedSectionGroupError(
                course_id,
                family,
                "active secondary sections have no required anchor: "
                f"{unresolved_children}",
                session,
            )

        for _, section in eligible_members:
            options.append(CourseOption((section,)))

    return tuple(options)


def load_course(
    connection,
    quarter,
    course_id,
    include_closed=False,
):
    record = get_course_sections(connection, quarter, course_id)

    if record is None:
        raise LookupError(
            f"Course {course_id!r} was not found for quarter {quarter!r}"
        )

    course_record = record["course"]
    options = build_course_options(
        course_record["course_id"],
        record["sections"],
        include_closed=include_closed,
    )

    return Course(
        course_record["title"],
        course_record["course_id"],
        options,
        units=_course_units(course_record),
        description=course_record.get("description"),
    )


def load_courses(
    connection,
    quarter,
    course_ids,
    include_closed=False,
):
    return [
        load_course(
            connection,
            quarter,
            course_id,
            include_closed=include_closed,
        )
        for course_id in course_ids
    ]


def _is_required_anchor(value):
    return isinstance(value, str) and value.strip().upper() == "R"


def _is_eligible(record, include_closed):
    if bool(record.get("course_cancelled")):
        return False

    return include_closed or not bool(record.get("class_closed"))


def _course_units(course_record):
    fixed_units = course_record.get("units_fixed")

    if fixed_units is not None:
        return fixed_units

    low = course_record.get("units_variable_low")
    high = course_record.get("units_variable_high")

    if low is not None and low == high:
        return low

    return None
