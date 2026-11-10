import os
import sqlite3
from pathlib import Path
from typing import List, Union


MIGRATIONS_DIR = Path(__file__).with_name("migrations")
PathLike = Union[str, os.PathLike]


def connect_catalog(path: PathLike) -> sqlite3.Connection:
    database_path = os.fspath(path)

    if database_path != ":memory:":
        Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database_path, timeout=5.0)
    _configure_connection(connection)

    if database_path != ":memory:":
        connection.execute("PRAGMA journal_mode = WAL")

    return connection


def migrate_catalog(
    path_or_connection: Union[PathLike, sqlite3.Connection]
) -> List[int]:
    owns_connection = not isinstance(path_or_connection, sqlite3.Connection)
    connection = (
        connect_catalog(path_or_connection)
        if owns_connection
        else path_or_connection
    )

    try:
        _configure_connection(connection)
        _ensure_migration_table(connection)
        applied = {
            row["version"]
            for row in connection.execute(
                "SELECT version FROM schema_migrations"
            ).fetchall()
        }
        applied_now = []

        for migration_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version_text, separator, _ = migration_path.stem.partition("_")

            if not separator or not version_text.isdigit():
                raise ValueError(
                    "Migration filenames must begin with a numeric version: "
                    f"{migration_path.name}"
                )

            version = int(version_text)

            if version in applied:
                continue

            _apply_migration(connection, version, migration_path)
            applied.add(version)
            applied_now.append(version)

        return applied_now
    finally:
        if owns_connection:
            connection.close()


def _ensure_migration_table(connection: sqlite3.Connection) -> None:
    had_transaction = connection.in_transaction
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    if not had_transaction:
        connection.commit()


def _configure_connection(connection: sqlite3.Connection) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")

    if not connection.in_transaction:
        connection.execute("PRAGMA foreign_keys = ON")


def _apply_migration(
    connection: sqlite3.Connection,
    version: int,
    migration_path: Path,
) -> None:
    savepoint = f"catalog_migration_{version}"
    had_transaction = connection.in_transaction

    if had_transaction:
        connection.execute(f"SAVEPOINT {savepoint}")
    else:
        connection.execute("BEGIN IMMEDIATE")

    try:
        script = migration_path.read_text(encoding="utf-8")

        for statement in _sql_statements(script):
            connection.execute(statement)

        connection.execute(
            """
            INSERT INTO schema_migrations (version, name)
            VALUES (?, ?)
            """,
            (version, migration_path.name),
        )

        if had_transaction:
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        else:
            connection.commit()
    except Exception:
        if had_transaction:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        else:
            connection.rollback()
        raise


def _sql_statements(script: str):
    buffer = []

    for line in script.splitlines():
        buffer.append(line)
        statement = "\n".join(buffer).strip()

        if statement and sqlite3.complete_statement(statement):
            yield statement
            buffer = []

    remainder = "\n".join(buffer).strip()

    if remainder and not remainder.startswith("--"):
        raise ValueError("Migration contains an incomplete SQL statement")
