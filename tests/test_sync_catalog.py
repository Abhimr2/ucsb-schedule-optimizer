import copy
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from catalog_database import connect_catalog  # noqa: E402
from catalog_repository import get_catalog_counts  # noqa: E402
from sync_catalog import (  # noqa: E402
    CatalogSyncError,
    LEGACY_DATABASE_PATH,
    load_api_key,
    main,
    sync_quarter,
)


def raw_course(
    course_id="CMPSC     16  ",
    title="PROBLEM SOLVING I",
    enroll_code="08128",
    quarter="20271",
):
    return {
        "quarter": quarter,
        "courseId": course_id,
        "title": title,
        "description": "Fixture course",
        "subjectArea": course_id.split()[0],
        "deptCode": course_id.split()[0],
        "college": "ENGR",
        "objLevelCode": "U",
        "unitsFixed": 4.0,
        "instructionType": "LEC",
        "onLineCourse": False,
        "generalEducation": [],
        "classSections": [
            {
                "enrollCode": enroll_code,
                "section": "0100",
                "typeInstruction": "LEC",
                "classClosed": False,
                "courseCancelled": False,
                "secondaryStatus": "R",
                "timeLocations": [
                    {
                        "days": "M W",
                        "beginTime": "09:30",
                        "endTime": "10:45",
                        "building": "ILP",
                        "room": "1203",
                        "roomCapacity": 75,
                    }
                ],
                "instructors": [
                    {
                        "instructor": "INSTRUCTOR A",
                        "functionCode": "Teaching and in charge",
                    }
                ],
                "concurrentCourses": [],
            }
        ],
    }


class FakeClient:
    def __init__(
        self,
        classes=None,
        expected_total=None,
        error=None,
        quarter="20271",
        fetched_total=None,
        source_total_shortfall=None,
        pagination_complete=True,
    ):
        self.classes = list(classes or [])
        self.expected_total = (
            len(self.classes) if expected_total is None else expected_total
        )
        self.fetched_total = (
            len(self.classes) if fetched_total is None else fetched_total
        )
        self.source_total_shortfall = (
            self.expected_total - self.fetched_total
            if source_total_shortfall is None
            else source_total_shortfall
        )
        self.pagination_complete = pagination_complete
        self.error = error
        self.quarter = quarter
        self.calls = []

    def fetch_all_classes(self, quarter, page_size=100):
        self.calls.append((quarter, page_size))
        if self.error is not None:
            raise self.error
        return {
            "quarter": self.quarter,
            "expected_total": self.expected_total,
            "fetched_total": self.fetched_total,
            "source_total_shortfall": self.source_total_shortfall,
            "pagination_complete": self.pagination_complete,
            "classes": copy.deepcopy(self.classes),
        }


class TestEnvironmentLoading(unittest.TestCase):
    def test_existing_environment_value_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("UCSB_API_KEY=file-value\n", encoding="utf-8")

            result = load_api_key(
                env_path=env_path,
                environ={"UCSB_API_KEY": "environment-value"},
            )

        self.assertEqual("environment-value", result)

    def test_dotenv_value_is_loaded_without_modifying_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text(
                "# local settings\nexport UCSB_API_KEY='file-value'\n",
                encoding="utf-8",
            )
            environment = {}

            result = load_api_key(env_path=env_path, environ=environment)

        self.assertEqual("file-value", result)
        self.assertEqual({}, environment)

    def test_missing_key_has_safe_error(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_path = Path(directory) / "missing.env"

            with self.assertRaises(CatalogSyncError) as raised:
                load_api_key(env_path=missing_path, environ={})

        self.assertIn("UCSB_API_KEY is not set", str(raised.exception))
        self.assertNotIn("file-value", str(raised.exception))


class TestSyncQuarter(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "catalog.db"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_fake_sync_runs_end_to_end_and_returns_json_summary(self):
        client = FakeClient([raw_course()])

        summary = sync_quarter(
            "20271",
            self.database_path,
            "test-key",
            page_size=50,
            client=client,
        )

        self.assertEqual([("20271", 50)], client.calls)
        self.assertEqual("20271", summary["quarter"])
        self.assertEqual(1, summary["expected_total"])
        self.assertEqual(1, summary["fetched_total"])
        self.assertEqual(0, summary["source_total_shortfall"])
        self.assertTrue(summary["pagination_complete"])
        self.assertIsNone(summary["warning"])
        self.assertEqual(
            {
                "courses": 1,
                "sections": 1,
                "meetings": 1,
                "instructors": 1,
                "general_education": 0,
            },
            summary["database_counts"],
        )
        self.assertIsInstance(summary["sync_run_id"], int)
        self.assertIsInstance(summary["duration_seconds"], float)
        self.assertGreaterEqual(summary["duration_seconds"], 0)
        self.assertEqual(str(self.database_path.resolve()), summary["database_path"])
        json.dumps(summary)

    def test_repeated_sync_is_idempotent(self):
        client = FakeClient([raw_course()])

        first = sync_quarter(
            "20271", self.database_path, "test-key", client=client
        )
        second = sync_quarter(
            "20271", self.database_path, "test-key", client=client
        )

        self.assertEqual(first["database_counts"], second["database_counts"])
        connection = connect_catalog(self.database_path)
        try:
            self.assertEqual(
                2,
                connection.execute(
                    "SELECT COUNT(*) FROM catalog_sync_runs WHERE status = 'completed'"
                ).fetchone()[0],
            )
        finally:
            connection.close()

    def test_failed_replacement_preserves_previous_catalog(self):
        sync_quarter(
            "20271",
            self.database_path,
            "test-key",
            client=FakeClient([raw_course()]),
        )
        duplicate_enroll_code_catalog = [
            raw_course(),
            raw_course(
                course_id="MATH       4A ",
                title="LINEAR ALGEBRA",
                enroll_code="08128",
            ),
        ]

        with self.assertRaises(sqlite3.IntegrityError):
            sync_quarter(
                "20271",
                self.database_path,
                "test-key",
                client=FakeClient(duplicate_enroll_code_catalog),
            )

        connection = connect_catalog(self.database_path)
        try:
            self.assertEqual(
                {
                    "courses": 1,
                    "sections": 1,
                    "meetings": 1,
                    "instructors": 1,
                    "general_education": 0,
                },
                get_catalog_counts(connection, "20271"),
            )
            course_ids = connection.execute(
                "SELECT course_id FROM courses WHERE quarter = '20271'"
            ).fetchall()
            self.assertEqual(["CMPSC 16"], [row[0] for row in course_ids])
        finally:
            connection.close()

    def test_bounded_source_shortfall_is_accepted_and_recorded(self):
        classes = [
            raw_course(),
            raw_course(
                course_id="MATH       4A ",
                title="LINEAR ALGEBRA",
                enroll_code="30106",
            ),
        ]
        client = FakeClient(classes, expected_total=3)

        summary = sync_quarter(
            "20271",
            self.database_path,
            "test-key",
            client=client,
        )

        self.assertEqual(3, summary["expected_total"])
        self.assertEqual(2, summary["fetched_total"])
        self.assertEqual(1, summary["source_total_shortfall"])
        self.assertIsNotNone(summary["warning"])

        connection = connect_catalog(self.database_path)
        try:
            sync_run = connection.execute(
                """
                SELECT expected_total, fetched_total
                FROM catalog_sync_runs
                WHERE status = 'completed'
                ORDER BY id DESC
                LIMIT 1
                """
            ).fetchone()
            self.assertEqual(3, sync_run["expected_total"])
            self.assertEqual(2, sync_run["fetched_total"])
        finally:
            connection.close()

    def test_excessive_source_shortfall_preserves_previous_catalog(self):
        sync_quarter(
            "20271",
            self.database_path,
            "test-key",
            client=FakeClient([raw_course()]),
        )
        client = FakeClient([raw_course()], expected_total=12)

        with self.assertRaisesRegex(CatalogSyncError, "safety limit"):
            sync_quarter(
                "20271",
                self.database_path,
                "test-key",
                client=client,
            )

        connection = connect_catalog(self.database_path)
        try:
            self.assertEqual(1, get_catalog_counts(connection, "20271")["courses"])
            sync_runs = connection.execute(
                "SELECT COUNT(*) FROM catalog_sync_runs"
            ).fetchone()[0]
            self.assertEqual(1, sync_runs)
        finally:
            connection.close()

    def test_incomplete_pagination_is_rejected_before_database_changes(self):
        client = FakeClient(
            [raw_course()],
            expected_total=2,
            pagination_complete=False,
        )

        with self.assertRaisesRegex(CatalogSyncError, "did not complete"):
            sync_quarter(
                "20271",
                self.database_path,
                "test-key",
                client=client,
            )

        self.assertFalse(self.database_path.exists())

    def test_inconsistent_or_negative_shortfalls_are_rejected_before_db(self):
        invalid_clients = [
            FakeClient(
                [raw_course()],
                expected_total=2,
                source_total_shortfall=0,
            ),
            FakeClient(
                [raw_course()],
                expected_total=1,
                source_total_shortfall=-1,
            ),
            FakeClient(
                [raw_course()],
                expected_total=1,
                fetched_total=2,
            ),
        ]

        for client in invalid_clients:
            with self.subTest(client=client):
                with self.assertRaises(CatalogSyncError):
                    sync_quarter(
                        "20271",
                        self.database_path,
                        "test-key",
                        client=client,
                    )

        self.assertFalse(self.database_path.exists())

    def test_other_quarters_are_preserved(self):
        sync_quarter(
            "20272",
            self.database_path,
            "test-key",
            client=FakeClient(
                [raw_course(quarter="20272")],
                quarter="20272",
            ),
        )
        sync_quarter(
            "20271",
            self.database_path,
            "test-key",
            client=FakeClient([raw_course()]),
        )

        connection = connect_catalog(self.database_path)
        try:
            self.assertEqual(1, get_catalog_counts(connection, "20271")["courses"])
            self.assertEqual(1, get_catalog_counts(connection, "20272")["courses"])
        finally:
            connection.close()

    def test_rejects_legacy_database_and_unsafe_arguments(self):
        client = FakeClient([raw_course()])

        with self.assertRaisesRegex(ValueError, "legacy"):
            sync_quarter(
                "20271",
                LEGACY_DATABASE_PATH,
                "test-key",
                client=client,
            )
        with self.assertRaisesRegex(ValueError, "five digits"):
            sync_quarter(
                "winter-2027",
                self.database_path,
                "test-key",
                client=client,
            )
        with self.assertRaisesRegex(ValueError, "between 1 and 100"):
            sync_quarter(
                "20271",
                self.database_path,
                "test-key",
                page_size=101,
                client=client,
            )
        self.assertEqual([], client.calls)


class TestSyncCLI(unittest.TestCase):
    def test_cli_prints_progress_and_summary_without_key(self):
        summary = {
            "quarter": "20271",
            "expected_total": 1,
            "fetched_total": 1,
            "source_total_shortfall": 0,
            "pagination_complete": True,
            "warning": None,
            "database_counts": {"courses": 1},
            "sync_run_id": 1,
            "duration_seconds": 0.01,
            "database_path": "/tmp/catalog.db",
        }
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            patch("sync_catalog.load_api_key", return_value="private-test-key"),
            patch("sync_catalog.sync_quarter", return_value=summary) as sync,
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(
                [
                    "--quarter",
                    "20271",
                    "--database",
                    "/tmp/catalog.db",
                    "--page-size",
                    "25",
                    "--max-source-shortfall",
                    "4",
                ]
            )

        self.assertEqual(0, exit_code)
        self.assertEqual("", stderr.getvalue())
        self.assertIn("Syncing UCSB quarter 20271", stdout.getvalue())
        self.assertNotIn("private-test-key", stdout.getvalue())
        sync.assert_called_once_with(
            "20271",
            Path("/tmp/catalog.db").resolve(),
            "private-test-key",
            page_size=25,
            max_source_shortfall=4,
        )

    def test_cli_rejects_invalid_quarter_without_loading_key(self):
        stderr = io.StringIO()

        with (
            patch("sync_catalog.load_api_key") as load_key,
            redirect_stderr(stderr),
        ):
            exit_code = main(["--quarter", "2027"])

        self.assertNotEqual(0, exit_code)
        self.assertIn("five digits", stderr.getvalue())
        load_key.assert_not_called()

    def test_cli_rejects_negative_max_shortfall_without_loading_key(self):
        stderr = io.StringIO()

        with (
            patch("sync_catalog.load_api_key") as load_key,
            redirect_stderr(stderr),
        ):
            exit_code = main(["--max-source-shortfall", "-1"])

        self.assertNotEqual(0, exit_code)
        self.assertIn("nonnegative", stderr.getvalue())
        load_key.assert_not_called()


if __name__ == "__main__":
    unittest.main()
