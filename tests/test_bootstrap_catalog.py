import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from bootstrap_catalog import (
    CatalogBootstrapError,
    bootstrap_catalog,
    catalog_is_ready,
)
from catalog_database import connect_catalog, migrate_catalog


QUARTER = "20271"


def write_ready_catalog(path, title="READY"):
    connection = connect_catalog(path)
    try:
        migrate_catalog(connection)
        connection.execute(
            """
            INSERT INTO courses (
                quarter, course_id, raw_course_id, title, source_order, source_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (QUARTER, "TEST 1", "TEST 1", title, 0, "{}"),
        )
        connection.execute(
            """
            INSERT INTO sections (
                quarter, course_id, enroll_code, section_number,
                source_order, source_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (QUARTER, "TEST 1", "00001", "0100", 0, "{}"),
        )
        connection.execute(
            """
            INSERT INTO catalog_sync_runs (
                quarter, started_at, finished_at, expected_total,
                fetched_total, status
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (QUARTER, "2026-11-09", "2026-11-09", 1, 1, "completed"),
        )
        connection.commit()
    finally:
        connection.close()


class CatalogBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "catalog.db"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_ready_catalog_does_not_sync_without_refresh(self):
        write_ready_catalog(self.database_path)

        def unexpected_sync(*_args):
            self.fail("sync should not run")

        result = bootstrap_catalog(
            self.database_path,
            api_key="test",
            sync_function=unexpected_sync,
        )

        self.assertTrue(catalog_is_ready(self.database_path))
        self.assertFalse(result["synced"])

    def test_missing_catalog_is_built_and_swapped_in(self):
        def fake_sync(_quarter, path, _api_key):
            write_ready_catalog(path, title="DOWNLOADED")
            return {"fetched_total": 1}

        result = bootstrap_catalog(
            self.database_path,
            api_key="test",
            sync_function=fake_sync,
        )

        self.assertTrue(result["synced"])
        self.assertTrue(catalog_is_ready(self.database_path))
        connection = sqlite3.connect(self.database_path)
        try:
            title = connection.execute("SELECT title FROM courses").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual("DOWNLOADED", title)

    def test_corrupt_catalog_is_replaced(self):
        self.database_path.write_bytes(b"not a database")

        def fake_sync(_quarter, path, _api_key):
            write_ready_catalog(path)
            return {"fetched_total": 1}

        bootstrap_catalog(
            self.database_path,
            api_key="test",
            sync_function=fake_sync,
        )

        self.assertTrue(catalog_is_ready(self.database_path))

    def test_failed_refresh_preserves_valid_catalog(self):
        write_ready_catalog(self.database_path, title="ORIGINAL")

        def failed_sync(*_args):
            raise RuntimeError("source unavailable")

        result = bootstrap_catalog(
            self.database_path,
            api_key="test",
            refresh=True,
            sync_function=failed_sync,
        )

        self.assertFalse(result["synced"])
        self.assertIn("source unavailable", result["warning"])
        self.assertTrue(catalog_is_ready(self.database_path))

    def test_failed_first_sync_does_not_leave_a_database(self):
        def failed_sync(*_args):
            raise RuntimeError("source unavailable")

        with self.assertRaises(CatalogBootstrapError):
            bootstrap_catalog(
                self.database_path,
                api_key="test",
                sync_function=failed_sync,
            )

        self.assertFalse(self.database_path.exists())


if __name__ == "__main__":
    unittest.main()
