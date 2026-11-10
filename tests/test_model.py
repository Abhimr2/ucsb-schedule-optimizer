import unittest
from dataclasses import FrozenInstanceError

from model import (
    Block,
    CombinationLimitExceeded,
    Course,
    CourseOption,
    Scheduler,
    Section,
    option_conflicts,
    time_conflicts,
)


def make_block(
    days=("M",),
    start=540,
    end=590,
    meeting_type="lecture",
    **kwargs,
):
    return Block(days, start, end, meeting_type, **kwargs)


def make_section(
    enroll_code,
    blocks=(),
    section_number="0100",
    instruction_type="lecture",
    **kwargs,
):
    return Section(
        enroll_code,
        section_number,
        instruction_type,
        blocks,
        **kwargs,
    )


def make_option(enroll_code, blocks, **section_kwargs):
    return CourseOption(
        (make_section(enroll_code, blocks, **section_kwargs),)
    )


def default_preferences(**overrides):
    preferences = {
        "preferredDays": ["M", "W", "F"],
        "preferredStart": 600,
        "preferredEnd": 840,
        "minimumGap": 60,
        "dayWeight": 1,
        "timeWeight": 2,
        "gapWeight": 3,
    }
    preferences.update(overrides)
    return preferences


class TestBlock(unittest.TestCase):
    def test_days_are_immutable_and_aliases_are_read_only(self):
        source_days = ["m", "W", "M"]
        block = Block(source_days, 540, 590, "lecture")
        source_days.append("F")

        self.assertEqual(("M", "W"), block.days)
        self.assertEqual(540, block.startTime)
        self.assertEqual(590, block.endTime)
        self.assertEqual("lecture", block.type)

        with self.assertRaises((AttributeError, FrozenInstanceError)):
            block.startTime = 600

    def test_timed_status_requires_days_times_and_non_tba_status(self):
        self.assertTrue(make_block().is_timed)
        self.assertFalse(make_block(days=()).is_timed)
        self.assertFalse(make_block(start=None, end=None).is_timed)
        self.assertFalse(make_block(is_tba=True).is_timed)

    def test_invalid_time_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "earlier"):
            make_block(start=600, end=600)


class TestSection(unittest.TestCase):
    def test_zero_and_multiple_blocks_are_preserved(self):
        empty = make_section("00001")
        first = make_block(("M",), 540, 590)
        second = make_block(("W",), 600, 650)
        multiple = make_section("00002", [first, second])

        self.assertEqual((), empty.blocks)
        self.assertTrue(empty.is_incomplete)
        self.assertEqual((first, second), multiple.blocks)
        self.assertEqual((first, second), multiple.timed_blocks)
        self.assertFalse(multiple.is_incomplete)

    def test_tba_and_incomplete_helpers(self):
        tba = make_block((), None, None, is_tba=True)
        section = make_section("00001", (make_block(), tba))

        self.assertTrue(section.has_tba)
        self.assertTrue(section.is_incomplete)
        self.assertEqual((section.blocks[0],), section.timed_blocks)

    def test_availability_helpers(self):
        open_section = make_section(
            "00001",
            enrolled_total=8,
            max_enroll=10,
        )
        full_section = make_section(
            "00002",
            enrolled_total=10,
            max_enroll=10,
        )
        closed_section = make_section("00003", closed=True)
        cancelled_section = make_section("00004", cancelled=True)
        unknown_section = make_section("00005")

        self.assertEqual(2, open_section.available_seats)
        self.assertEqual("open", open_section.availability)
        self.assertTrue(open_section.is_available)
        self.assertEqual("full", full_section.availability)
        self.assertFalse(full_section.is_available)
        self.assertEqual("closed", closed_section.availability)
        self.assertEqual("cancelled", cancelled_section.availability)
        self.assertEqual("unknown", unknown_section.availability)
        self.assertTrue(unknown_section.is_available)


class TestCourseOption(unittest.TestCase):
    def test_flattens_multi_meeting_sections_in_section_order(self):
        lecture_blocks = (
            make_block(("M", "W"), 540, 590),
            make_block(("F",), 600, 650),
        )
        discussion_block = make_block(("R",), 660, 710, "discussion")
        lecture = make_section("01000", lecture_blocks)
        discussion = make_section(
            "01001",
            (discussion_block,),
            section_number="0101",
            instruction_type="discussion",
        )
        option = CourseOption((lecture, discussion))

        self.assertEqual(
            lecture_blocks + (discussion_block,),
            option.blocks,
        )
        self.assertEqual(("01000", "01001"), option.enroll_codes)
        self.assertEqual(option.enroll_codes, option.key)
        self.assertEqual(option.blocks, tuple(option))

    def test_detects_internal_conflicts_across_required_sections(self):
        lecture = make_section(
            "01000",
            (make_block(("M",), 540, 600),),
        )
        conflicting_discussion = make_section(
            "01001",
            (make_block(("M",), 570, 630, "discussion"),),
            section_number="0101",
            instruction_type="discussion",
        )
        back_to_back_discussion = make_section(
            "01002",
            (make_block(("M",), 600, 650, "discussion"),),
            section_number="0102",
            instruction_type="discussion",
        )

        self.assertTrue(
            CourseOption((lecture, conflicting_discussion)).has_internal_conflict
        )
        self.assertFalse(
            CourseOption((lecture, back_to_back_discussion)).has_internal_conflict
        )

    def test_incomplete_status_includes_no_block_sections(self):
        option = CourseOption((make_section("01000"),))
        self.assertTrue(option.is_incomplete)
        self.assertFalse(option.has_tba)


class TestCourse(unittest.TestCase):
    def test_create_options_returns_explicit_immutable_options(self):
        option_a = make_option("00001", (make_block(("M",)),))
        option_b = make_option("00002", (make_block(("T",)),))
        source_options = [option_a, option_b]
        course = Course("Test Course", "TEST 1", source_options)
        source_options.clear()

        self.assertEqual((option_a, option_b), course.options)
        self.assertIs(course.options, course.create_options())

    def test_course_can_have_zero_options(self):
        course = Course("Unavailable Course", "NONE 1", ())
        self.assertEqual((), course.create_options())


class TestConflicts(unittest.TestCase):
    def test_time_conflict_boundaries_and_untimed_blocks(self):
        cases = [
            (
                make_block(("M",), 540, 600),
                make_block(("M",), 570, 630),
                True,
            ),
            (
                make_block(("M",), 540, 600),
                make_block(("M",), 600, 660),
                False,
            ),
            (
                make_block(("M",), 540, 600),
                make_block(("T",), 570, 630),
                False,
            ),
            (
                make_block(("M",), 540, 660),
                make_block(("M",), 570, 600, "discussion"),
                True,
            ),
            (
                make_block((), None, None, is_tba=True),
                make_block(("M",), 570, 630),
                False,
            ),
        ]

        for block_a, block_b, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(expected, time_conflicts(block_a, block_b))

    def test_option_conflicts_checks_every_block(self):
        multi_meeting = make_option(
            "00001",
            (
                make_block(("M",), 540, 590),
                make_block(("F",), 600, 650),
            ),
        )
        safe = make_option(
            "00002",
            (make_block(("M",), 600, 650),),
        )
        conflicting = make_option(
            "00003",
            (make_block(("F",), 630, 680),),
        )

        self.assertFalse(option_conflicts(multi_meeting, safe))
        self.assertTrue(option_conflicts(multi_meeting, conflicting))


class TestScheduler(unittest.TestCase):
    def setUp(self):
        math_section = make_section(
            "M-S",
            (make_block(("F",), 540, 590, "discussion"),),
            section_number="0101",
            instruction_type="discussion",
        )
        self.math_option_1 = CourseOption(
            (
                make_section(
                    "M-1",
                    (make_block(("M", "W"), 540, 590),),
                ),
                math_section,
            )
        )
        self.math_option_2 = CourseOption(
            (
                make_section(
                    "M-2",
                    (make_block(("T", "R"), 660, 735),),
                    section_number="0200",
                ),
                math_section,
            )
        )

        cs_section = make_section(
            "C-S",
            (make_block(("F",), 600, 650, "discussion"),),
            section_number="0101",
            instruction_type="discussion",
        )
        self.cs_option_1 = CourseOption(
            (
                make_section(
                    "C-1",
                    (make_block(("M", "W"), 600, 650),),
                ),
                cs_section,
            )
        )
        self.cs_option_2 = CourseOption(
            (
                make_section(
                    "C-2",
                    (make_block(("T", "R"), 690, 765),),
                    section_number="0200",
                ),
                cs_section,
            )
        )

        self.music_option = CourseOption(
            (
                make_section(
                    "MU-L",
                    (make_block(("M", "W"), 780, 830),),
                ),
                make_section(
                    "MU-S",
                    (make_block(("F",), 780, 830, "discussion"),),
                    section_number="0101",
                    instruction_type="discussion",
                ),
            )
        )
        self.writing_option = CourseOption(
            (
                make_section(
                    "W-L",
                    (make_block(("T", "R"), 840, 915),),
                ),
                make_section(
                    "W-S",
                    (make_block(("W",), 840, 890, "discussion"),),
                    section_number="0101",
                    instruction_type="discussion",
                ),
            )
        )

        self.math = Course(
            "Math",
            "MATH",
            (self.math_option_1, self.math_option_2),
        )
        self.cs = Course(
            "Computer Science",
            "CS",
            (self.cs_option_1, self.cs_option_2),
        )
        self.music = Course("Music", "MUS", (self.music_option,))
        self.writing = Course("Writing", "WRIT", (self.writing_option,))
        self.scheduler = Scheduler(
            (self.math, self.cs, self.music, self.writing)
        )
        self.valid_schedule = (
            self.math_option_1,
            self.cs_option_1,
            self.music_option,
            self.writing_option,
        )
        self.invalid_schedule = (
            self.math_option_2,
            self.cs_option_2,
            self.music_option,
            self.writing_option,
        )

    def test_backtracking_restores_original_course_order(self):
        schedules = self.scheduler.generate_schedules()

        self.assertEqual(3, len(schedules))
        self.assertEqual(
            ("M-1", "M-S"),
            schedules[0][0].enroll_codes,
        )
        self.assertEqual(("C-1", "C-S"), schedules[0][1].enroll_codes)
        self.assertIs(self.music_option, schedules[0][2])
        self.assertIs(self.writing_option, schedules[0][3])
        self.assertEqual(
            [
                ("M-1", "C-1"),
                ("M-1", "C-2"),
                ("M-2", "C-1"),
            ],
            [
                (schedule[0].enroll_codes[0], schedule[1].enroll_codes[0])
                for schedule in schedules
            ],
        )

    def test_result_limit_still_evaluates_all_combinations_for_stats(self):
        schedules = self.scheduler.generate_schedules(limit=1)

        self.assertEqual(1, len(schedules))
        self.assertEqual(
            {"raw_count": 4, "valid_count": 3, "returned_count": 1},
            self.scheduler.last_generation_stats,
        )

    def test_internal_conflicting_options_are_rejected(self):
        bad_option = make_option(
            "BAD",
            (
                make_block(("M",), 540, 600),
                make_block(("M",), 570, 630),
            ),
        )
        good_option = make_option(
            "GOOD",
            (make_block(("T",), 540, 600),),
        )
        scheduler = Scheduler(
            (Course("Course", "C", (bad_option, good_option)),)
        )

        self.assertFalse(scheduler.schedule_is_valid((bad_option,)))
        self.assertEqual([(good_option,)], scheduler.generate_schedules())
        self.assertEqual(
            {"raw_count": 2, "valid_count": 1, "returned_count": 1},
            scheduler.last_generation_stats,
        )

    def test_combination_limit_raises_before_generation(self):
        course_a = Course(
            "A",
            "A",
            tuple(
                make_option(f"A-{index}", (make_block(("M",), 60 * index, 60 * index + 30),))
                for index in range(3)
            ),
        )
        course_b = Course(
            "B",
            "B",
            tuple(
                make_option(f"B-{index}", (make_block(("T",), 60 * index, 60 * index + 30),))
                for index in range(4)
            ),
        )
        scheduler = Scheduler((course_a, course_b))

        with self.assertRaises(CombinationLimitExceeded) as context:
            scheduler.generate_schedules(combination_limit=11)

        self.assertEqual(12, context.exception.raw_count)
        self.assertEqual(11, context.exception.combination_limit)
        self.assertIsNone(scheduler.last_generation_stats)

    def test_zero_option_course_returns_no_schedules(self):
        scheduler = Scheduler((Course("Empty", "E", ()),))

        self.assertEqual([], scheduler.generate_schedules())
        self.assertEqual(
            {"raw_count": 0, "valid_count": 0, "returned_count": 0},
            scheduler.last_generation_stats,
        )

    def test_empty_course_selection_returns_one_empty_schedule(self):
        scheduler = Scheduler(())

        self.assertEqual([()], scheduler.generate_schedules())
        self.assertEqual(
            {"raw_count": 1, "valid_count": 1, "returned_count": 1},
            scheduler.last_generation_stats,
        )

    def test_schedule_validation_and_block_flattening(self):
        self.assertTrue(self.scheduler.schedule_is_valid(self.valid_schedule))
        self.assertFalse(self.scheduler.schedule_is_valid(self.invalid_schedule))
        self.assertTrue(self.scheduler.schedule_is_valid(()))
        self.assertEqual(
            self.scheduler.get_all_schedule_blocks(self.valid_schedule),
            self.scheduler.get_allScheduleBlocks(self.valid_schedule),
        )
        self.assertEqual(
            8,
            len(self.scheduler.get_all_schedule_blocks(self.valid_schedule)),
        )

    def test_sort_by_days_keeps_timed_blocks_in_time_order(self):
        days = self.scheduler.sort_by_days(self.valid_schedule)

        self.assertEqual(5, len(days))
        self.assertEqual([540, 600, 780], [block.start_time for block in days[0]])
        self.assertEqual(
            [540, 600, 780, 840],
            [block.start_time for block in days[2]],
        )

    def test_scoring_behavior(self):
        self.assertAlmostEqual(
            10 / 12,
            self.scheduler.score_days(
                self.valid_schedule,
                ["M", "W", "F"],
            ),
        )
        self.assertAlmostEqual(
            6 / 12,
            self.scheduler.score_time_range(
                self.valid_schedule,
                600,
                840,
            ),
        )
        self.assertAlmostEqual(
            3 / 7,
            self.scheduler.score_gap(self.valid_schedule, 60),
        )

        expected = ((10 / 12) + (6 / 12 * 2) + (3 / 7 * 3)) / 6 * 100
        self.assertAlmostEqual(
            expected,
            self.scheduler.score_schedule(
                self.valid_schedule,
                default_preferences(),
            ),
        )

    def test_untimed_blocks_are_neutral_in_all_scores(self):
        no_blocks = make_option("NONE", ())
        tba = make_option(
            "TBA",
            (make_block((), None, None, is_tba=True),),
        )
        scheduler = Scheduler(())
        schedule = (no_blocks, tba)

        self.assertEqual(1.0, scheduler.score_days(schedule, ["M"]))
        self.assertEqual(1.0, scheduler.score_time_range(schedule, 540, 1020))
        self.assertEqual(1.0, scheduler.score_gap(schedule, 60))
        self.assertEqual(
            100.0,
            scheduler.score_schedule(schedule, default_preferences()),
        )

    def test_minimum_gap_boundary_is_inclusive(self):
        first = make_option("FIRST", (make_block(("M",), 540, 590),))
        second = make_option("SECOND", (make_block(("M",), 605, 655),))
        schedule = (first, second)
        scheduler = Scheduler(())

        self.assertEqual(1.0, scheduler.score_gap(schedule, 15))
        self.assertEqual(0.0, scheduler.score_gap(schedule, 16))

    def test_scoring_rejects_negative_and_zero_total_weights(self):
        for field in ("dayWeight", "timeWeight", "gapWeight"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    self.scheduler.score_schedule(
                        self.valid_schedule,
                        default_preferences(**{field: -1}),
                    )

        with self.assertRaisesRegex(ValueError, "positive total"):
            self.scheduler.score_schedule(
                self.valid_schedule,
                default_preferences(
                    dayWeight=0,
                    timeWeight=0,
                    gapWeight=0,
                ),
            )

    def test_deterministic_score_descending_ranking(self):
        option_b = make_option(
            "B",
            (make_block(("M",), 600, 650),),
        )
        option_a = make_option(
            "A",
            (make_block(("M",), 600, 650),),
        )
        option_c = make_option(
            "C",
            (make_block(("T",), 300, 350),),
        )
        scheduler = Scheduler(
            (Course("Ranked", "R", (option_b, option_a, option_c)),)
        )
        preferences = default_preferences(
            preferredDays=["M"],
            preferredStart=540,
            preferredEnd=1020,
            dayWeight=1,
            timeWeight=1,
            gapWeight=0,
        )

        first = scheduler.rank_schedules(preferences, limit=3)
        second = scheduler.rank_schedules(preferences, limit=3)

        self.assertEqual(["A", "B", "C"], [row[0].key[0] for row in first])
        self.assertEqual(first, second)
        self.assertIs(option_a, first[0][0])
        self.assertGreaterEqual(
            scheduler.score_schedule(first[0], preferences),
            scheduler.score_schedule(first[-1], preferences),
        )
        self.assertEqual(
            {"raw_count": 3, "valid_count": 3, "returned_count": 3},
            scheduler.last_generation_stats,
        )


if __name__ == "__main__":
    unittest.main()
