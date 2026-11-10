import base64
import hashlib
import json
import sqlite3

from catalog_repository import (
    get_course_sections,
    search_courses as repository_search_courses,
)
from course_loader import (
    UnresolvedSectionGroupError,
    load_course,
    load_courses,
)
from model import CombinationLimitExceeded, Scheduler, WEEKDAYS


class ApiError(Exception):
    def __init__(
        self,
        status_code,
        code,
        message,
        field_errors=None,
        retryable=False,
    ):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field_errors = dict(field_errors or {})
        self.retryable = retryable
        super().__init__(message)


class CatalogService:
    def __init__(
        self,
        connection,
        quarter="20271",
        term="2027-winter",
        term_label="Winter 2027",
        combination_limit=1_000_000,
    ):
        if connection is None:
            raise ValueError("connection is required")
        if not isinstance(quarter, str) or not quarter.strip():
            raise ValueError("quarter must be a non-empty string")
        if not isinstance(term, str) or not term.strip():
            raise ValueError("term must be a non-empty string")
        if not isinstance(term_label, str) or not term_label.strip():
            raise ValueError("term_label must be a non-empty string")
        if (
            isinstance(combination_limit, bool)
            or not isinstance(combination_limit, int)
            or combination_limit < 1
        ):
            raise ValueError("combination_limit must be a positive integer")

        self.connection = connection
        self.quarter = quarter.strip()
        self.term = term.strip()
        self.term_label = term_label.strip()
        self.combination_limit = combination_limit

    def status_payload(self):
        return {
            "status": "ok",
            "api_version": "v1",
            "freshness": self._freshness(),
        }

    def search_courses(self, query, limit=20, cursor=None):
        normalized_query = self._validate_search_query(query)
        validated_limit = self._validate_search_limit(limit)
        offset = self._decode_cursor(cursor, normalized_query)

        try:
            rows = repository_search_courses(
                self.connection,
                self.quarter,
                normalized_query,
                limit=validated_limit + 1,
                offset=offset,
            )
            items = [self._course_summary(row) for row in rows[:validated_limit]]
        except sqlite3.Error:
            raise self._data_unavailable() from None

        next_cursor = None
        if len(rows) > validated_limit:
            next_cursor = self._encode_cursor(
                normalized_query,
                offset + validated_limit,
            )

        return {
            "items": items,
            "next_cursor": next_cursor,
            "freshness": self._freshness(),
        }

    def course_detail(self, course_code):
        canonical_code = self._resolve_course_code(course_code)

        try:
            record = get_course_sections(
                self.connection,
                self.quarter,
                canonical_code,
            )
            course = load_course(
                self.connection,
                self.quarter,
                canonical_code,
                include_closed=True,
            )
        except sqlite3.Error:
            raise self._data_unavailable() from None
        except UnresolvedSectionGroupError:
            raise self._data_unavailable() from None

        course_record = record["course"]
        options = [
            self._serialize_option(course, option, index)
            for index, option in enumerate(course.options)
        ]

        return {
            "code": course.code,
            "name": course.name,
            "term": self.term,
            "term_label": self.term_label,
            "units": self._course_units(course_record),
            "grading_option": course_record.get("grading_option"),
            "description": course.description,
            "department": course_record.get("department_code"),
            "college": course_record.get("college"),
            "online": self._optional_boolean(course_record.get("online")),
            "general_education": record["general_education"],
            "option_count": len(options),
            "options": options,
            "freshness": self._freshness(),
        }

    def generate_schedules(self, payload):
        request = self._validate_generation_request(payload)

        try:
            courses = load_courses(
                self.connection,
                self.quarter,
                request["course_codes"],
                include_closed=False,
            )
        except sqlite3.Error:
            raise self._data_unavailable() from None
        except (LookupError, UnresolvedSectionGroupError):
            raise self._data_unavailable() from None

        preferences = request["preferences"]
        scoring_days = preferences["preferred_days"] or list(WEEKDAYS)
        scoring_preferences = {
            "preferred_days": scoring_days,
            "preferred_start": preferences["preferred_start"],
            "preferred_end": preferences["preferred_end"],
            "minimum_gap": preferences["minimum_gap"],
            "weights": {
                "days": preferences["weights"]["days"],
                "time": preferences["weights"]["time_range"],
                "gap": preferences["weights"]["gaps"],
            },
        }
        scheduler = Scheduler(courses)

        try:
            ranked = scheduler.rank_schedules(
                scoring_preferences,
                limit=request["result_limit"],
                combination_limit=self.combination_limit,
            )
        except CombinationLimitExceeded as error:
            raise ApiError(
                422,
                "COMBINATION_LIMIT_EXCEEDED",
                "The selected courses produce too many schedule combinations.",
                {
                    "course_codes": (
                        f"{error.raw_count} combinations exceed the "
                        f"limit of {error.combination_limit}."
                    )
                },
                False,
            ) from None

        request_id = self._request_id(request)
        schedules = [
            self._serialize_schedule(
                request_id,
                rank,
                courses,
                schedule,
                scheduler,
                scoring_preferences,
                preferences,
            )
            for rank, schedule in enumerate(ranked, start=1)
        ]
        stats = scheduler.last_generation_stats

        return {
            "request_id": request_id,
            "term": self.term,
            "raw_schedule_count": stats["raw_count"],
            "valid_schedule_count": stats["valid_count"],
            "returned_schedule_count": stats["returned_count"],
            "schedules": schedules,
            "freshness": self._freshness(),
        }

    def _freshness(self):
        try:
            row = self.connection.execute(
                """
                SELECT finished_at
                FROM catalog_sync_runs
                WHERE quarter = ? AND status = 'completed'
                ORDER BY id DESC
                LIMIT 1
                """,
                (self.quarter,),
            ).fetchone()
        except sqlite3.Error:
            raise self._data_unavailable() from None

        if row is None:
            raise self._data_unavailable()

        return {
            "term": self.term,
            "term_label": self.term_label,
            "dataset_kind": "snapshot",
            "is_live": False,
            "source": "ucsb_academic_curriculums_api",
            "last_updated_at": row["finished_at"],
            "expires_at": None,
        }

    def _course_summary(self, row):
        grading_row = self.connection.execute(
            """
            SELECT grading_option
            FROM courses
            WHERE quarter = ? AND course_id = ?
            """,
            (self.quarter, row["course_id"]),
        ).fetchone()

        return {
            "code": row["course_id"],
            "name": row["title"],
            "term": self.term,
            "units": self._course_units(row),
            "grading_option": grading_row["grading_option"],
            "department": row.get("department_code"),
            "college": row.get("college"),
            "online": self._optional_boolean(row.get("online")),
            "section_count": row["section_count"],
        }

    def _serialize_option(self, course, option, index):
        sections = [
            self._serialize_section(
                course,
                section,
                option.sections[0].enroll_code if section_index else None,
            )
            for section_index, section in enumerate(option.sections)
        ]

        return {
            "id": self._option_id(course.code, option.enroll_codes),
            "index": index,
            "enroll_codes": list(option.enroll_codes),
            "section_numbers": [
                section.section_number for section in option.sections
            ],
            "available": all(section.is_available for section in option.sections),
            "has_tba": option.has_tba,
            "is_incomplete": option.is_incomplete,
            "sections": sections,
        }

    def _serialize_section(self, course, section, parent_enroll_code=None):
        meetings = [
            self._serialize_meeting(
                course,
                section,
                block,
                block_index,
                parent_enroll_code,
            )
            for block_index, block in enumerate(section.blocks)
        ]
        locations = []
        location_keys = set()

        for meeting in meetings:
            location = meeting["location"]
            if location is None:
                continue
            key = (location["building"], location["room"])
            if key not in location_keys:
                location_keys.add(key)
                locations.append(location)

        return {
            "enroll_code": section.enroll_code,
            "section_number": section.section_number,
            "parent_enroll_code": parent_enroll_code,
            "instruction_type": section.instruction_type,
            "session": section.session,
            "secondary_status": section.secondary_status,
            "availability": section.availability,
            "enrollment": {
                "enrolled": section.enrolled_total,
                "capacity": section.max_enroll,
                "available": section.available_seats,
            },
            "instructors": list(section.instructors),
            "locations": locations,
            "has_tba": section.has_tba,
            "is_incomplete": section.is_incomplete,
            "meetings": meetings,
        }

    def _serialize_meeting(
        self,
        course,
        section,
        block,
        block_index,
        parent_enroll_code=None,
    ):
        location = self._location(block.building, block.room)

        return {
            "id": self._meeting_id(
                course.code,
                section.enroll_code,
                block_index,
            ),
            "course_code": course.code,
            "course_name": course.name,
            "enroll_code": section.enroll_code,
            "section_number": section.section_number,
            "parent_enroll_code": parent_enroll_code,
            "meeting_type": block.meeting_type,
            "days": list(block.days),
            "start_time": block.start_time,
            "end_time": block.end_time,
            "instructors": list(section.instructors),
            "location": location,
            "is_tba": block.is_tba,
            "availability": section.availability,
        }

    def _serialize_schedule(
        self,
        request_id,
        rank,
        courses,
        schedule,
        scheduler,
        scoring_preferences,
        requested_preferences,
    ):
        day_ratio = scheduler.score_days(
            schedule,
            scoring_preferences["preferred_days"],
        )
        time_ratio = scheduler.score_time_range(
            schedule,
            scoring_preferences["preferred_start"],
            scoring_preferences["preferred_end"],
        )
        gap_ratio = scheduler.score_gap(
            schedule,
            scoring_preferences["minimum_gap"],
        )
        score = scheduler.score_schedule(schedule, scoring_preferences)
        meetings = []
        course_options = []

        for course, option in zip(courses, schedule):
            option_meeting_ids = []
            parent_enroll_code = (
                option.sections[0].enroll_code
                if len(option.sections) > 1
                else None
            )

            for section_index, section in enumerate(option.sections):
                parent = parent_enroll_code if section_index else None
                for block_index, block in enumerate(section.blocks):
                    meeting = self._serialize_meeting(
                        course,
                        section,
                        block,
                        block_index,
                        parent,
                    )
                    meetings.append(meeting)
                    option_meeting_ids.append(meeting["id"])

            course_options.append(
                {
                    "course_code": course.code,
                    "course_name": course.name,
                    "option_id": self._option_id(
                        course.code,
                        option.enroll_codes,
                    ),
                    "section_numbers": [
                        section.section_number for section in option.sections
                    ],
                    "enroll_codes": list(option.enroll_codes),
                    "meeting_ids": option_meeting_ids,
                }
            )

        weights = requested_preferences["weights"]

        return {
            "id": f"{request_id}:{rank}",
            "rank": rank,
            "score": round(score, 4),
            "score_breakdown": {
                "days": {
                    "ratio": round(day_ratio, 6),
                    "weight": weights["days"],
                },
                "time_range": {
                    "ratio": round(time_ratio, 6),
                    "weight": weights["time_range"],
                },
                "gaps": {
                    "ratio": round(gap_ratio, 6),
                    "weight": weights["gaps"],
                },
            },
            "summary": self._schedule_summary(
                meetings,
                requested_preferences,
            ),
            "course_options": course_options,
            "meetings": meetings,
        }

    def _schedule_summary(self, meetings, preferences):
        timed = [
            meeting
            for meeting in meetings
            if (
                not meeting["is_tba"]
                and meeting["start_time"] is not None
                and meeting["end_time"] is not None
                and meeting["days"]
            )
        ]
        class_days = sorted(
            {day for meeting in timed for day in meeting["days"]},
            key=WEEKDAYS.index,
        )
        preferred_days = set(preferences["preferred_days"])
        outside_time_range_count = 0
        non_preferred_day_count = 0
        day_blocks = {day: [] for day in WEEKDAYS}

        for meeting in timed:
            outside = (
                meeting["start_time"] < preferences["preferred_start"]
                or meeting["end_time"] > preferences["preferred_end"]
            )
            if outside:
                outside_time_range_count += len(meeting["days"])

            for day in meeting["days"]:
                if preferred_days and day not in preferred_days:
                    non_preferred_day_count += 1
                if day in day_blocks:
                    day_blocks[day].append(
                        (meeting["start_time"], meeting["end_time"])
                    )

        gaps = []
        for blocks in day_blocks.values():
            blocks.sort()
            gaps.extend(
                blocks[index + 1][0] - blocks[index][1]
                for index in range(len(blocks) - 1)
            )

        return {
            "class_days": len(class_days),
            "days": class_days,
            "earliest_start": min(
                (meeting["start_time"] for meeting in timed),
                default=None,
            ),
            "latest_end": max(
                (meeting["end_time"] for meeting in timed),
                default=None,
            ),
            "minimum_gap": min(gaps, default=0),
            "outside_time_range_count": outside_time_range_count,
            "non_preferred_day_count": non_preferred_day_count,
            "tba_meeting_count": sum(
                meeting["is_tba"] for meeting in meetings
            ),
        }

    def _validate_generation_request(self, payload):
        if not isinstance(payload, dict):
            raise ApiError(
                400,
                "INVALID_REQUEST",
                "The request body must be a JSON object.",
                {"body": "Must be an object."},
                False,
            )

        allowed = {"term", "course_codes", "preferences", "result_limit"}
        unsupported = sorted(set(payload) - allowed)
        if unsupported:
            raise ApiError(
                400,
                "INVALID_REQUEST",
                "The request contains unsupported fields.",
                {
                    field: "Unsupported field."
                    for field in unsupported
                },
                False,
            )

        self._validate_requested_term(payload.get("term", self.term))
        course_codes = self._validate_course_codes(payload.get("course_codes"))
        preferences = self._validate_preferences(payload.get("preferences"))
        result_limit = payload.get("result_limit", 3)

        if not self._integer_in_range(result_limit, 1, 10):
            raise ApiError(
                400,
                "INVALID_REQUEST",
                "result_limit must be an integer from 1 through 10.",
                {"result_limit": "Must be an integer from 1 through 10."},
                False,
            )

        return {
            "term": self.term,
            "course_codes": course_codes,
            "preferences": preferences,
            "result_limit": result_limit,
        }

    def _validate_requested_term(self, term):
        if not isinstance(term, str) or not term.strip():
            raise ApiError(
                400,
                "INVALID_REQUEST",
                "term must be a non-empty string.",
                {"term": "Must be a non-empty string."},
                False,
            )
        if term.strip() != self.term:
            raise ApiError(
                409,
                "TERM_UNAVAILABLE",
                "The requested term is unavailable.",
                {"term": f"Only {self.term} is available."},
                False,
            )

    def _validate_course_codes(self, value):
        errors = {}

        if not isinstance(value, list):
            raise ApiError(
                422,
                "INVALID_COURSE_SELECTION",
                "course_codes must be a list of two to five course codes.",
                {"course_codes": "Must be a list of two to five course codes."},
                False,
            )

        if not 2 <= len(value) <= 5:
            errors["course_codes"] = "Select between two and five courses."

        normalized = []
        for index, course_code in enumerate(value):
            if not isinstance(course_code, str) or not course_code.strip():
                errors[f"course_codes.{index}"] = "Must be a non-empty string."
                continue
            normalized.append(" ".join(course_code.strip().upper().split()))

        if len(set(normalized)) != len(normalized):
            errors["course_codes"] = "Course codes must be unique."

        canonical = []
        if not errors:
            try:
                for index, course_code in enumerate(normalized):
                    row = self.connection.execute(
                        """
                        SELECT course_id
                        FROM courses
                        WHERE quarter = ? AND course_id = ? COLLATE NOCASE
                        """,
                        (self.quarter, course_code),
                    ).fetchone()
                    if row is None:
                        errors[f"course_codes.{index}"] = "Unknown course code."
                    else:
                        canonical.append(row["course_id"])
            except sqlite3.Error:
                raise self._data_unavailable() from None

        if errors:
            raise ApiError(
                422,
                "INVALID_COURSE_SELECTION",
                "The course selection is invalid.",
                errors,
                False,
            )

        return canonical

    def _validate_preferences(self, value):
        if not isinstance(value, dict):
            raise ApiError(
                422,
                "INVALID_PREFERENCES",
                "preferences must be an object.",
                {"preferences": "Must be an object."},
                False,
            )

        allowed = {
            "preferred_days",
            "preferred_start",
            "preferred_end",
            "minimum_gap",
            "weights",
        }
        unsupported = sorted(set(value) - allowed)
        if unsupported:
            raise ApiError(
                400,
                "INVALID_REQUEST",
                "The request contains unsupported preference fields.",
                {
                    f"preferences.{field}": "Unsupported field."
                    for field in unsupported
                },
                False,
            )

        errors = {}
        preferred_days = value.get("preferred_days")
        if not isinstance(preferred_days, list):
            errors["preferences.preferred_days"] = "Must be a list."
        else:
            if any(
                not isinstance(day, str) or day not in WEEKDAYS
                for day in preferred_days
            ):
                errors["preferences.preferred_days"] = (
                    "Days must be selected from M, T, W, R, and F."
                )
            elif len(set(preferred_days)) != len(preferred_days):
                errors["preferences.preferred_days"] = "Days must be unique."

        preferred_start = value.get("preferred_start")
        preferred_end = value.get("preferred_end")
        if not self._integer_in_range(preferred_start, 0, 1439):
            errors["preferences.preferred_start"] = (
                "Must be an integer from 0 through 1439."
            )
        if not self._integer_in_range(preferred_end, 0, 1439):
            errors["preferences.preferred_end"] = (
                "Must be an integer from 0 through 1439."
            )
        if (
            self._integer_in_range(preferred_start, 0, 1439)
            and self._integer_in_range(preferred_end, 0, 1439)
            and preferred_start >= preferred_end
        ):
            errors["preferences.preferred_end"] = (
                "Must be greater than preferred_start."
            )

        minimum_gap = value.get("minimum_gap")
        if not self._integer_in_range(minimum_gap, 15, 720):
            errors["preferences.minimum_gap"] = (
                "Must be an integer from 15 through 720."
            )

        weights = value.get("weights")
        validated_weights = None
        if not isinstance(weights, dict):
            errors["preferences.weights"] = "Must be an object."
        else:
            allowed_weights = {"days", "time_range", "gaps"}
            unsupported_weights = sorted(set(weights) - allowed_weights)
            for field in unsupported_weights:
                errors[f"preferences.weights.{field}"] = "Unsupported field."

            validated_weights = {}
            for field in ("days", "time_range", "gaps"):
                weight = weights.get(field)
                if not self._integer_in_range(weight, 0, 3):
                    errors[f"preferences.weights.{field}"] = (
                        "Must be an integer from 0 through 3."
                    )
                else:
                    validated_weights[field] = weight

            if (
                len(validated_weights) == 3
                and sum(validated_weights.values()) == 0
            ):
                errors["preferences.weights"] = (
                    "At least one weight must be greater than zero."
                )

        if errors:
            code = (
                "INVALID_REQUEST"
                if any("Unsupported field." == message for message in errors.values())
                else "INVALID_PREFERENCES"
            )
            status_code = 400 if code == "INVALID_REQUEST" else 422
            raise ApiError(
                status_code,
                code,
                "The schedule preferences are invalid.",
                errors,
                False,
            )

        return {
            "preferred_days": list(preferred_days),
            "preferred_start": preferred_start,
            "preferred_end": preferred_end,
            "minimum_gap": minimum_gap,
            "weights": validated_weights,
        }

    def _resolve_course_code(self, course_code):
        if not isinstance(course_code, str) or not course_code.strip():
            raise ApiError(
                404,
                "COURSE_NOT_FOUND",
                "The requested course was not found.",
                {},
                False,
            )

        normalized = " ".join(course_code.strip().upper().split())

        try:
            row = self.connection.execute(
                """
                SELECT course_id
                FROM courses
                WHERE quarter = ? AND course_id = ? COLLATE NOCASE
                """,
                (self.quarter, normalized),
            ).fetchone()
        except sqlite3.Error:
            raise self._data_unavailable() from None

        if row is None:
            raise ApiError(
                404,
                "COURSE_NOT_FOUND",
                "The requested course was not found.",
                {},
                False,
            )

        return row["course_id"]

    def _validate_search_query(self, query):
        if not isinstance(query, str) or len(query.strip()) < 2:
            raise ApiError(
                400,
                "INVALID_REQUEST",
                "q must contain at least two characters.",
                {"q": "Must contain at least two characters."},
                False,
            )
        return query.strip()

    def _validate_search_limit(self, limit):
        if not self._integer_in_range(limit, 1, 50):
            raise ApiError(
                400,
                "INVALID_REQUEST",
                "limit must be an integer from 1 through 50.",
                {"limit": "Must be an integer from 1 through 50."},
                False,
            )
        return limit

    def _encode_cursor(self, query, offset):
        data = json.dumps(
            {
                "offset": offset,
                "quarter": self.quarter,
                "query": query.casefold(),
                "version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")

    def _decode_cursor(self, cursor, query):
        if cursor is None:
            return 0
        if not isinstance(cursor, str) or not cursor:
            raise self._invalid_cursor()

        try:
            padding = "=" * (-len(cursor) % 4)
            decoded = base64.b64decode(
                cursor + padding,
                altchars=b"-_",
                validate=True,
            )
            data = json.loads(decoded.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError
            if set(data) != {"offset", "quarter", "query", "version"}:
                raise ValueError
            if data["version"] != 1:
                raise ValueError
            if data["quarter"] != self.quarter:
                raise ValueError
            if data["query"] != query.casefold():
                raise ValueError
            if (
                isinstance(data["offset"], bool)
                or not isinstance(data["offset"], int)
                or data["offset"] < 0
            ):
                raise ValueError
        except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
            raise self._invalid_cursor() from None

        return data["offset"]

    def _invalid_cursor(self):
        return ApiError(
            400,
            "INVALID_REQUEST",
            "cursor is invalid for this search.",
            {"cursor": "Invalid or expired cursor."},
            False,
        )

    def _request_id(self, request):
        encoded = json.dumps(
            request,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()[:24]
        return f"sched_{digest}"

    def _option_id(self, course_code, enroll_codes):
        return ":".join(
            (self.term, course_code, "option", *enroll_codes)
        )

    def _meeting_id(self, course_code, enroll_code, block_index):
        return ":".join(
            (
                self.term,
                course_code,
                enroll_code,
                str(block_index),
            )
        )

    def _course_units(self, record):
        fixed = record.get("units_fixed")
        if fixed is not None:
            return fixed

        low = record.get("units_variable_low")
        high = record.get("units_variable_high")
        if low is not None and low == high:
            return low
        return None

    def _location(self, building, room):
        if building is None and room is None:
            return None

        label = " ".join(
            str(value).strip()
            for value in (building, room)
            if value is not None and str(value).strip()
        )
        return {
            "building": building,
            "room": room,
            "label": label or None,
        }

    def _optional_boolean(self, value):
        if value is None:
            return None
        return bool(value)

    def _integer_in_range(self, value, minimum, maximum):
        return (
            not isinstance(value, bool)
            and isinstance(value, int)
            and minimum <= value <= maximum
        )

    def _data_unavailable(self):
        return ApiError(
            503,
            "DATA_UNAVAILABLE",
            "Course data is temporarily unavailable.",
            {},
            True,
        )
