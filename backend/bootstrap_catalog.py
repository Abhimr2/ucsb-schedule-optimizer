from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from sync_catalog import (
    DEFAULT_DATABASE_PATH,
    DEFAULT_QUARTER,
    load_api_key,
    sync_quarter,
)


class CatalogBootstrapError(RuntimeError):
    pass


def catalog_is_ready(database_path, quarter=DEFAULT_QUARTER):
    path = Path(database_path).expanduser()
    if not path.is_file() or path.stat().st_size == 0:
        return False

    connection = None
    try:
        connection = sqlite3.connect(
            f"file:{path.resolve()}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        integrity = connection.execute("PRAGMA quick_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            return False

        course_count = connection.execute(
            "SELECT COUNT(*) FROM courses WHERE quarter = ?",
            (quarter,),
        ).fetchone()[0]
        section_count = connection.execute(
            "SELECT COUNT(*) FROM sections WHERE quarter = ?",
            (quarter,),
        ).fetchone()[0]
        completed_syncs = connection.execute(
            """
            SELECT COUNT(*)
            FROM catalog_sync_runs
            WHERE quarter = ? AND status = 'completed'
            """,
            (quarter,),
        ).fetchone()[0]
        return course_count > 0 and section_count > 0 and completed_syncs > 0
    except (OSError, sqlite3.Error):
        return False
    finally:
        if connection is not None:
            connection.close()


def bootstrap_catalog(
    database_path=DEFAULT_DATABASE_PATH,
    quarter=DEFAULT_QUARTER,
    api_key=None,
    refresh=False,
    sync_function=sync_quarter,
):
    target_path = Path(database_path).expanduser()
    existing_ready = catalog_is_ready(target_path, quarter)

    if existing_ready and not refresh:
        return {
            "status": "ready",
            "synced": False,
            "database_path": str(target_path),
            "quarter": quarter,
        }

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_handle = tempfile.NamedTemporaryFile(
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        dir=target_path.parent,
        delete=False,
    )
    temporary_path = Path(temporary_handle.name)
    temporary_handle.close()
    temporary_path.unlink(missing_ok=True)

    try:
        key = api_key if api_key is not None else load_api_key()
        summary = sync_function(quarter, temporary_path, key)

        if not catalog_is_ready(temporary_path, quarter):
            raise CatalogBootstrapError(
                "The downloaded catalog did not pass integrity checks."
            )

        _prepare_database_file(temporary_path)
        _remove_sidecars(target_path)
        os.replace(temporary_path, target_path)
        target_path.chmod(0o600)

        return {
            "status": "ready",
            "synced": True,
            "database_path": str(target_path),
            "quarter": quarter,
            "sync": summary,
        }
    except Exception as error:
        if existing_ready and catalog_is_ready(target_path, quarter):
            return {
                "status": "ready",
                "synced": False,
                "database_path": str(target_path),
                "quarter": quarter,
                "warning": str(error),
            }
        raise CatalogBootstrapError(
            "No valid course catalog is available."
        ) from error
    finally:
        temporary_path.unlink(missing_ok=True)
        _remove_sidecars(temporary_path)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default=str(DEFAULT_DATABASE_PATH))
    parser.add_argument("--quarter", default=DEFAULT_QUARTER)
    parser.add_argument("--refresh", action="store_true")
    arguments = parser.parse_args(argv)

    try:
        result = bootstrap_catalog(
            database_path=arguments.database,
            quarter=arguments.quarter,
            refresh=arguments.refresh,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (CatalogBootstrapError, OSError, sqlite3.Error, ValueError) as error:
        print(f"Catalog bootstrap failed: {error}")
        return 1


def _prepare_database_file(database_path):
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode = DELETE")
    finally:
        connection.close()


def _remove_sidecars(database_path):
    path = Path(database_path)
    Path(f"{path}-wal").unlink(missing_ok=True)
    Path(f"{path}-shm").unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
