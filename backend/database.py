from pathlib import Path
import sqlite3

backend_path = Path(__file__).resolve().parent
project_path = backend_path.parent

instance_path = project_path / "instance"
instance_path.mkdir(parents=True, exist_ok=True)

database_path = instance_path / "courses.db"

def get_con():
    con = sqlite3.connect(database_path)

    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")

    return con

def close_con(con):
    if con is not None:
        con.close()

def initialize():
    schema_path = backend_path / "schema.sql"
    schema = schema_path.read_text(encoding="utf-8")

    con = None

    try:
        con = get_con()
        con.executescript(schema)
        con.commit()

    except sqlite3.Error as error:
        if con is not None:
            con.rollback()

        print("SQLite error:", error)
        raise

    finally:
        close_con(con)

def get_all_meetings():
    con = None

    try:
        con = get_con()

        rows = con.execute(
            """
            SELECT
                name,
                code,
                lecture_id,
                meeting_type,
                days,
                start_time,
                end_time
            FROM meetings
            ORDER BY code, lecture_id
            """
        ).fetchall()

        return [dict(row) for row in rows]

    finally:
        close_con(con)

def get_all_courses():
    con = None

    try:
        con = get_con()

        rows = con.execute(
            """
            SELECT DISTINCT
                name,
                code
            FROM meetings
            ORDER BY code
            """
        ).fetchall()

        return [dict(row) for row in rows]

    finally:
        close_con(con)

def get_meetings_by_code(code):
    con = None

    try:
        con = get_con()

        rows = con.execute(
            """
            SELECT
                name,
                code,
                lecture_id,
                meeting_type,
                days,
                start_time,
                end_time
            FROM meetings
            WHERE code = ?
            ORDER BY lecture_id, meeting_type, days, start_time
            """,
            (code,),
        ).fetchall()

        return [dict(row) for row in rows]

    finally:
        close_con(con)