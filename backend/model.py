from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from numbers import Real


WEEKDAYS = ("M", "T", "W", "R", "F")
DEFAULT_COMBINATION_LIMIT = 1_000_000


@dataclass(frozen=True)
class Block:
    days: tuple[str, ...]
    start_time: int | None
    end_time: int | None
    meeting_type: str
    building: str | None = None
    room: str | None = None
    is_tba: bool = False

    def __post_init__(self):
        normalized_days = []
        for raw_day in self.days:
            if not isinstance(raw_day, str) or not raw_day.strip():
                raise ValueError("days must contain non-empty strings")
            day = raw_day.strip().upper()
            if day not in normalized_days:
                normalized_days.append(day)

        object.__setattr__(self, "days", tuple(normalized_days))
        _validate_optional_minute(self.start_time, "start_time")
        _validate_optional_minute(self.end_time, "end_time", allow_day_end=True)

        if (
            self.start_time is not None
            and self.end_time is not None
            and self.start_time >= self.end_time
        ):
            raise ValueError("start_time must be earlier than end_time")

        if not isinstance(self.meeting_type, str) or not self.meeting_type.strip():
            raise ValueError("meeting_type must be a non-empty string")

        if not isinstance(self.is_tba, bool):
            raise TypeError("is_tba must be a boolean")

    @property
    def is_timed(self):
        return (
            not self.is_tba
            and bool(self.days)
            and self.start_time is not None
            and self.end_time is not None
        )

    @property
    def startTime(self):
        return self.start_time

    @property
    def endTime(self):
        return self.end_time

    @property
    def type(self):
        return self.meeting_type


@dataclass(frozen=True)
class Section:
    enroll_code: str
    section_number: str
    instruction_type: str
    blocks: tuple[Block, ...] = field(default_factory=tuple)
    session: str | None = None
    secondary_status: str | None = None
    instructors: tuple = field(default_factory=tuple)
    closed: bool = False
    cancelled: bool = False
    enrolled_total: int | None = None
    max_enroll: int | None = None

    def __post_init__(self):
        for field_name in ("enroll_code", "section_number", "instruction_type"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")

        blocks = tuple(self.blocks)
        if any(not isinstance(block, Block) for block in blocks):
            raise TypeError("blocks must contain only Block objects")

        object.__setattr__(self, "blocks", blocks)
        object.__setattr__(self, "instructors", tuple(self.instructors))

        if not isinstance(self.closed, bool) or not isinstance(self.cancelled, bool):
            raise TypeError("closed and cancelled must be booleans")

        _validate_optional_nonnegative_integer(
            self.enrolled_total,
            "enrolled_total",
        )
        _validate_optional_nonnegative_integer(self.max_enroll, "max_enroll")

    @property
    def timed_blocks(self):
        return tuple(block for block in self.blocks if block.is_timed)

    @property
    def has_tba(self):
        return any(block.is_tba for block in self.blocks)

    @property
    def is_incomplete(self):
        return not self.blocks or any(not block.is_timed for block in self.blocks)

    @property
    def available_seats(self):
        if self.enrolled_total is None or self.max_enroll is None:
            return None
        return max(self.max_enroll - self.enrolled_total, 0)

    @property
    def availability(self):
        if self.cancelled:
            return "cancelled"
        if self.closed:
            return "closed"

        available_seats = self.available_seats
        if available_seats is None:
            return "unknown"
        if available_seats == 0:
            return "full"
        return "open"

    @property
    def is_available(self):
        return self.availability in {"open", "unknown"}


@dataclass(frozen=True)
class CourseOption:
    sections: tuple[Section, ...]

    def __post_init__(self):
        sections = tuple(self.sections)
        if not sections:
            raise ValueError("a course option must contain at least one section")
        if any(not isinstance(section, Section) for section in sections):
            raise TypeError("sections must contain only Section objects")
        if len({section.enroll_code for section in sections}) != len(sections):
            raise ValueError("a course option cannot repeat an enroll code")

        object.__setattr__(self, "sections", sections)

    @property
    def blocks(self):
        return tuple(
            block
            for section in self.sections
            for block in section.blocks
        )

    @property
    def enroll_codes(self):
        return tuple(section.enroll_code for section in self.sections)

    @property
    def key(self):
        return self.enroll_codes

    @property
    def is_incomplete(self):
        return any(section.is_incomplete for section in self.sections)

    @property
    def has_tba(self):
        return any(section.has_tba for section in self.sections)

    @property
    def has_internal_conflict(self):
        blocks = self.blocks
        for index, block_a in enumerate(blocks):
            for block_b in blocks[index + 1 :]:
                if time_conflicts(block_a, block_b):
                    return True
        return False

    def __iter__(self):
        return iter(self.blocks)

    def __len__(self):
        return len(self.blocks)


@dataclass(frozen=True)
class Course:
    name: str
    code: str
    options: tuple[CourseOption, ...]
    units: float | None = None
    description: str | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty string")
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("code must be a non-empty string")

        options = tuple(self.options)
        if any(not isinstance(option, CourseOption) for option in options):
            raise TypeError("options must contain only CourseOption objects")
        object.__setattr__(self, "options", options)

    def create_options(self):
        return self.options


class CombinationLimitExceeded(ValueError):
    def __init__(self, raw_count, combination_limit):
        self.raw_count = raw_count
        self.combination_limit = combination_limit
        super().__init__(
            f"raw schedule count {raw_count} exceeds combination limit "
            f"{combination_limit}"
        )


class Scheduler:
    def __init__(self, courses):
        self.courses = tuple(courses)
        if any(not isinstance(course, Course) for course in self.courses):
            raise TypeError("courses must contain only Course objects")
        self.last_generation_stats = None

    def generate_schedules(
        self,
        limit=None,
        combination_limit=DEFAULT_COMBINATION_LIMIT,
    ):
        limit = _validate_result_limit(limit)
        raw_count = self._checked_raw_count(combination_limit)
        returned = []
        valid_count = 0

        for schedule in self._iter_valid_schedules():
            valid_count += 1
            if limit is None or len(returned) < limit:
                returned.append(schedule)

        self.last_generation_stats = {
            "raw_count": raw_count,
            "valid_count": valid_count,
            "returned_count": len(returned),
        }
        return returned

    def rank_schedules(
        self,
        preferences,
        limit=3,
        combination_limit=DEFAULT_COMBINATION_LIMIT,
    ):
        limit = _validate_rank_limit(limit)
        raw_count = self._checked_raw_count(combination_limit)
        ranked = []
        valid_count = 0

        for schedule in self._iter_valid_schedules():
            valid_count += 1
            if limit == 0:
                continue

            ranked.append(
                (
                    self.score_schedule(schedule, preferences),
                    _schedule_key(schedule),
                    schedule,
                )
            )
            ranked.sort(key=lambda item: (-item[0], item[1]))
            if len(ranked) > limit:
                ranked.pop()

        schedules = [item[2] for item in ranked]
        self.last_generation_stats = {
            "raw_count": raw_count,
            "valid_count": valid_count,
            "returned_count": len(schedules),
        }
        return schedules

    def schedule_is_valid(self, schedule):
        schedule = tuple(schedule)
        if any(option.has_internal_conflict for option in schedule):
            return False

        for index, option_a in enumerate(schedule):
            for option_b in schedule[index + 1 :]:
                if option_conflicts(option_a, option_b):
                    return False
        return True

    def get_all_schedule_blocks(self, schedule):
        return tuple(
            block
            for course_option in schedule
            for block in course_option.blocks
        )

    def get_allScheduleBlocks(self, schedule):
        return self.get_all_schedule_blocks(schedule)

    def sort_by_days(self, schedule):
        blocks_by_day = {day: [] for day in WEEKDAYS}

        for block in self.get_all_schedule_blocks(schedule):
            if not block.is_timed:
                continue
            for day in block.days:
                if day in blocks_by_day:
                    blocks_by_day[day].append(block)

        for blocks in blocks_by_day.values():
            blocks.sort(key=lambda block: (block.start_time, block.end_time))

        return tuple(tuple(blocks_by_day[day]) for day in WEEKDAYS)

    def score_days(self, schedule, preferred_days):
        preferred_days = set(preferred_days)
        successful_occurrences = 0
        total_occurrences = 0

        for block in self.get_all_schedule_blocks(schedule):
            if not block.is_timed:
                continue
            total_occurrences += len(block.days)
            successful_occurrences += sum(
                day in preferred_days for day in block.days
            )

        if total_occurrences == 0:
            return 1.0
        return successful_occurrences / total_occurrences

    def score_time_range(self, schedule, preferred_start, preferred_end):
        _validate_preferred_time_range(preferred_start, preferred_end)
        successful_occurrences = 0
        total_occurrences = 0

        for block in self.get_all_schedule_blocks(schedule):
            if not block.is_timed:
                continue
            total_occurrences += len(block.days)
            if (
                block.start_time >= preferred_start
                and block.end_time <= preferred_end
            ):
                successful_occurrences += len(block.days)

        if total_occurrences == 0:
            return 1.0
        return successful_occurrences / total_occurrences

    def score_timeRange(self, schedule, preferredStart, preferredEnd):
        return self.score_time_range(schedule, preferredStart, preferredEnd)

    def score_gap(self, schedule, minimum_gap):
        _validate_nonnegative_number(minimum_gap, "minimum_gap")
        successful_gaps = 0
        total_gaps = 0

        for day_blocks in self.sort_by_days(schedule):
            for index in range(len(day_blocks) - 1):
                current_block = day_blocks[index]
                next_block = day_blocks[index + 1]
                gap = next_block.start_time - current_block.end_time
                total_gaps += 1

                if gap >= minimum_gap:
                    successful_gaps += 1

        if total_gaps == 0:
            return 1.0
        return successful_gaps / total_gaps

    def score_schedule(self, schedule, preferences):
        preferred_days = _preference_value(
            preferences,
            "preferred_days",
            "preferredDays",
            "days",
        )
        preferred_start = _preference_value(
            preferences,
            "preferred_start",
            "preferredStart",
            "start",
        )
        preferred_end = _preference_value(
            preferences,
            "preferred_end",
            "preferredEnd",
            "end",
        )
        minimum_gap = _preference_value(
            preferences,
            "minimum_gap",
            "minimumGap",
            "preferred_gap",
            "preferredGap",
            "gap",
        )
        weights = {
            "days": _preference_weight(preferences, "day"),
            "time": _preference_weight(preferences, "time"),
            "gap": _preference_weight(preferences, "gap"),
        }

        total_weight = sum(weights.values())
        if total_weight == 0:
            raise ValueError("preference weights must have a positive total")

        weighted_score = (
            self.score_days(schedule, preferred_days) * weights["days"]
            + self.score_time_range(
                schedule,
                preferred_start,
                preferred_end,
            )
            * weights["time"]
            + self.score_gap(schedule, minimum_gap) * weights["gap"]
        )
        return weighted_score / total_weight * 100

    def _checked_raw_count(self, combination_limit):
        combination_limit = _validate_combination_limit(combination_limit)
        raw_count = 1
        for course in self.courses:
            raw_count *= len(course.options)

        if raw_count > combination_limit:
            raise CombinationLimitExceeded(raw_count, combination_limit)
        return raw_count

    def _iter_valid_schedules(self):
        ordered_courses = sorted(
            enumerate(self.courses),
            key=lambda item: (len(item[1].options), item[0]),
        )
        selected_by_original_index = [None] * len(self.courses)
        selected_options = []

        def backtrack(ordered_index):
            if ordered_index == len(ordered_courses):
                yield tuple(selected_by_original_index)
                return

            original_index, course = ordered_courses[ordered_index]
            for option in course.options:
                if option.has_internal_conflict:
                    continue
                if any(
                    option_conflicts(option, selected)
                    for selected in selected_options
                ):
                    continue

                selected_by_original_index[original_index] = option
                selected_options.append(option)
                yield from backtrack(ordered_index + 1)
                selected_options.pop()
                selected_by_original_index[original_index] = None

        yield from backtrack(0)


def time_conflicts(meeting_a, meeting_b):
    if not isinstance(meeting_a, Block) or not isinstance(meeting_b, Block):
        raise TypeError("time_conflicts expects two Block objects")
    if not meeting_a.is_timed or not meeting_b.is_timed:
        return False
    if not set(meeting_a.days).intersection(meeting_b.days):
        return False

    return (
        meeting_a.start_time < meeting_b.end_time
        and meeting_b.start_time < meeting_a.end_time
    )


def option_conflicts(option_a, option_b):
    blocks_a = _blocks_from(option_a)
    blocks_b = _blocks_from(option_b)

    return any(
        time_conflicts(block_a, block_b)
        for block_a in blocks_a
        for block_b in blocks_b
    )


def _blocks_from(value):
    if isinstance(value, Block):
        return (value,)
    if isinstance(value, Section):
        return value.blocks
    if isinstance(value, CourseOption):
        return value.blocks

    try:
        blocks = tuple(value)
    except TypeError as error:
        raise TypeError(
            "an option must be a CourseOption, Section, Block, or iterable of Blocks"
        ) from error

    if any(not isinstance(block, Block) for block in blocks):
        raise TypeError("an option iterable must contain only Block objects")
    return blocks


def _schedule_key(schedule):
    return tuple(option.key for option in schedule)


def _validate_optional_minute(value, field_name, allow_day_end=False):
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer or None")

    maximum = 1440 if allow_day_end else 1439
    if not 0 <= value <= maximum:
        raise ValueError(f"{field_name} must be between 0 and {maximum}")


def _validate_optional_nonnegative_integer(value, field_name):
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be a non-negative integer or None")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _validate_result_limit(limit):
    if limit is None:
        return None
    return _validate_nonnegative_integer(limit, "limit")


def _validate_rank_limit(limit):
    return _validate_nonnegative_integer(limit, "limit")


def _validate_combination_limit(combination_limit):
    return _validate_nonnegative_integer(
        combination_limit,
        "combination_limit",
    )


def _validate_nonnegative_integer(value, field_name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be a non-negative integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _validate_nonnegative_number(value, field_name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{field_name} must be a non-negative number")
    if not isfinite(value) or value < 0:
        raise ValueError(f"{field_name} must be a finite non-negative number")
    return float(value)


def _validate_preferred_time_range(preferred_start, preferred_end):
    _validate_optional_minute(preferred_start, "preferred_start")
    _validate_optional_minute(
        preferred_end,
        "preferred_end",
        allow_day_end=True,
    )
    if preferred_start is None or preferred_end is None:
        raise ValueError("preferred_start and preferred_end are required")
    if preferred_start > preferred_end:
        raise ValueError("preferred_start cannot be later than preferred_end")


def _preference_value(preferences, *names):
    if not isinstance(preferences, dict):
        raise TypeError("preferences must be a dictionary")
    for name in names:
        if name in preferences:
            return preferences[name]
    raise KeyError(names[0])


def _preference_weight(preferences, kind):
    weight_names = {
        "day": ("day_weight", "dayWeight"),
        "time": ("time_weight", "timeWeight"),
        "gap": ("gap_weight", "gapWeight"),
    }

    for name in weight_names[kind]:
        if name in preferences:
            return _validate_nonnegative_number(preferences[name], name)

    nested_weights = preferences.get("weights")
    nested_name = {"day": "days", "time": "time", "gap": "gap"}[kind]
    if isinstance(nested_weights, dict) and nested_name in nested_weights:
        return _validate_nonnegative_number(
            nested_weights[nested_name],
            f"weights.{nested_name}",
        )

    raise KeyError(weight_names[kind][0])
