from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

from catalog_database import connect_catalog, migrate_catalog
from catalog_normalizer import normalize_catalog
from catalog_repository import replace_quarter_catalog
from ucsb_client import UCSBCurriculumClient


API_KEY_ENV_NAME = "UCSB_API_KEY"
DEFAULT_QUARTER = "20271"
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 100
DEFAULT_MAX_SOURCE_SHORTFALL = 10
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
DEFAULT_ENV_PATH = BACKEND_DIR / ".env"
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "instance" / "catalog.db"
LEGACY_DATABASE_PATH = PROJECT_ROOT / "instance" / "courses.db"
_QUARTER_PATTERN = re.compile(r"\d{5}\Z")


class CatalogSyncError(RuntimeError):
    pass


def load_api_key(env_path=DEFAULT_ENV_PATH, environ=None):
    environment = os.environ if environ is None else environ
    existing = environment.get(API_KEY_ENV_NAME)

    if isinstance(existing, str) and existing.strip():
        return existing.strip()

    path = Path(env_path).expanduser()

    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        lines = []
    except OSError as error:
        raise CatalogSyncError(f"Unable to read environment file: {path}") from error

    for line in lines:
        parsed = _parse_dotenv_assignment(line)
        if parsed is None:
            continue
        name, value = parsed
        if name == API_KEY_ENV_NAME and value:
            return value

    raise CatalogSyncError(
        f"{API_KEY_ENV_NAME} is not set. Add it to the environment or {path}."
    )


def sync_quarter(
    quarter,
    database_path,
    api_key,
    page_size=DEFAULT_PAGE_SIZE,
    client=None,
    max_source_shortfall=DEFAULT_MAX_SOURCE_SHORTFALL,
):
    validated_quarter = _validate_quarter(quarter)
    validated_page_size = _validate_page_size(page_size)
    validated_max_shortfall = _validate_max_source_shortfall(
        max_source_shortfall
    )
    safe_database_path = _validate_database_path(database_path)

    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("api_key must be a non-empty string")

    started = time.monotonic()
    curriculum_client = client or UCSBCurriculumClient(api_key)
    response = curriculum_client.fetch_all_classes(
        validated_quarter,
        page_size=validated_page_size,
    )

    if not isinstance(response, dict):
        raise CatalogSyncError("The UCSB client returned an invalid result")

    response_quarter = str(response.get("quarter", "")).strip()
    expected_total = response.get("expected_total")
    reported_fetched_total = response.get("fetched_total")
    source_total_shortfall = response.get("source_total_shortfall")
    pagination_complete = response.get("pagination_complete")
    classes = response.get("classes")

    if response_quarter != validated_quarter:
        raise CatalogSyncError(
            "The UCSB client returned data for a different quarter"
        )
    if (
        isinstance(expected_total, bool)
        or not isinstance(expected_total, int)
        or expected_total < 0
    ):
        raise CatalogSyncError("The UCSB client returned an invalid expected total")
    if not isinstance(classes, list):
        raise CatalogSyncError("The UCSB client returned an invalid classes collection")

    fetched_total = len(classes)
    if (
        isinstance(reported_fetched_total, bool)
        or not isinstance(reported_fetched_total, int)
        or reported_fetched_total < 0
    ):
        raise CatalogSyncError("The UCSB client returned an invalid fetched total")
    if fetched_total != reported_fetched_total:
        raise CatalogSyncError(
            "The UCSB client reported a fetched total that does not match "
            "the returned classes"
        )
    if (
        isinstance(source_total_shortfall, bool)
        or not isinstance(source_total_shortfall, int)
        or source_total_shortfall < 0
    ):
        raise CatalogSyncError("The UCSB client returned an invalid source shortfall")
    if pagination_complete is not True:
        raise CatalogSyncError("Catalog pagination did not complete")

    calculated_shortfall = expected_total - fetched_total
    if calculated_shortfall < 0 or source_total_shortfall != calculated_shortfall:
        raise CatalogSyncError(
            "The UCSB client returned inconsistent catalog totals"
        )
    if source_total_shortfall > validated_max_shortfall:
        raise CatalogSyncError(
            "UCSB source shortfall exceeds the configured safety limit: "
            f"{source_total_shortfall} missing, limit {validated_max_shortfall}"
        )

    warning = None
    if source_total_shortfall:
        warning = (
            f"UCSB advertised {expected_total} courses but returned "
            f"{fetched_total} after complete pagination "
            f"(source shortfall: {source_total_shortfall})."
        )

    normalized_courses = normalize_catalog(classes)
    connection = None

    try:
        connection = connect_catalog(safe_database_path)
        migrate_catalog(connection)
        repository_result = replace_quarter_catalog(
            connection,
            validated_quarter,
            normalized_courses,
            expected_total,
        )
    finally:
        if connection is not None:
            connection.close()

    database_counts = {
        key: repository_result[key]
        for key in (
            "courses",
            "sections",
            "meetings",
            "instructors",
            "general_education",
        )
    }

    return {
        "quarter": validated_quarter,
        "expected_total": expected_total,
        "fetched_total": fetched_total,
        "source_total_shortfall": source_total_shortfall,
        "pagination_complete": pagination_complete,
        "warning": warning,
        "database_counts": database_counts,
        "sync_run_id": repository_result["sync_run_id"],
        "duration_seconds": round(time.monotonic() - started, 3),
        "database_path": _display_database_path(safe_database_path),
    }


def main(argv=None):
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    api_key = None

    try:
        quarter = _validate_quarter(arguments.quarter)
        page_size = _validate_page_size(arguments.page_size)
        max_source_shortfall = _validate_max_source_shortfall(
            arguments.max_source_shortfall
        )
        database_path = _validate_database_path(arguments.database)
        api_key = load_api_key()

        print(
            f"Syncing UCSB quarter {quarter} to "
            f"{_display_database_path(database_path)}...",
            flush=True,
        )
        summary = sync_quarter(
            quarter,
            database_path,
            api_key,
            page_size=page_size,
            max_source_shortfall=max_source_shortfall,
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    except (
        CatalogSyncError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        sqlite3.Error,
    ) as error:
        message = str(error)
        if api_key:
            message = message.replace(api_key, "[redacted]")
        print(f"Catalog sync failed: {message}", file=sys.stderr)
        return 1


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Import UCSB Academic Curriculums data into SQLite."
    )
    parser.add_argument(
        "--quarter",
        default=DEFAULT_QUARTER,
        help=f"five-digit UCSB quarter code (default: {DEFAULT_QUARTER})",
    )
    parser.add_argument(
        "--database",
        default=str(DEFAULT_DATABASE_PATH),
        help=f"catalog database path (default: {DEFAULT_DATABASE_PATH})",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f"courses per API page, 1-{MAX_PAGE_SIZE}",
    )
    parser.add_argument(
        "--max-source-shortfall",
        type=int,
        default=DEFAULT_MAX_SOURCE_SHORTFALL,
        help=(
            "maximum accepted difference between UCSB's advertised and "
            f"returned totals (default: {DEFAULT_MAX_SOURCE_SHORTFALL})"
        ),
    )
    return parser


def _parse_dotenv_assignment(line):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[7:].lstrip()

    name, separator, raw_value = stripped.partition("=")
    if not separator:
        return None

    name = name.strip()
    value = raw_value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    elif " #" in value:
        value = value.split(" #", 1)[0].rstrip()

    return name, value


def _validate_quarter(quarter):
    if not isinstance(quarter, str) or _QUARTER_PATTERN.fullmatch(quarter) is None:
        raise ValueError("quarter must be exactly five digits, such as 20271")
    return quarter


def _validate_page_size(page_size):
    if isinstance(page_size, bool) or not isinstance(page_size, int):
        raise TypeError("page_size must be an integer")
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")
    return page_size


def _validate_max_source_shortfall(max_source_shortfall):
    if isinstance(max_source_shortfall, bool) or not isinstance(
        max_source_shortfall, int
    ):
        raise TypeError("max_source_shortfall must be an integer")
    if max_source_shortfall < 0:
        raise ValueError("max_source_shortfall must be nonnegative")
    return max_source_shortfall


def _validate_database_path(database_path):
    if not isinstance(database_path, (str, os.PathLike)):
        raise TypeError("database_path must be a filesystem path")

    raw_path = os.fspath(database_path)
    if not raw_path.strip():
        raise ValueError("database_path must not be empty")
    if raw_path == ":memory:":
        return raw_path

    path = Path(raw_path).expanduser().resolve(strict=False)
    if path == LEGACY_DATABASE_PATH.resolve(strict=False):
        raise ValueError(
            "Refusing to overwrite the legacy instance/courses.db database"
        )
    if path.exists() and path.is_dir():
        raise ValueError("database_path must point to a file, not a directory")
    return path


def _display_database_path(database_path):
    if database_path == ":memory:":
        return database_path
    return str(Path(database_path))


if __name__ == "__main__":
    raise SystemExit(main())
