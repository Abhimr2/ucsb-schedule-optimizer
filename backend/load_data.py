import csv
import sqlite3
from pathlib import Path
from database import initialize, get_con, close_con


backend_path = Path(__file__).resolve().parent
project_path = backend_path.parent
csv_path = project_path / "data" / "sample_courses.csv"


def load_data():
    meeting_rows = []

    with csv_path.open(newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)

        for row in reader:
            meeting_rows.append(
                (
                    row["name"],
                    row["code"],
                    row["lectureID"],
                    row["type"],
                    row["days"],
                    row["startTime"],
                    row["endTime"],
                )
            )

    initialize()

    con = None

    try:
        con = get_con()

        con.execute("DELETE FROM meetings")

        con.executemany(
            """
            INSERT INTO meetings (
                name,
                code,
                lecture_id,
                meeting_type,
                days,
                start_time,
                end_time
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            meeting_rows,
        )

        con.commit()
        print(f"Loaded {len(meeting_rows)} meeting rows")

    except sqlite3.Error as error:
        if con is not None:
            con.rollback()

        print("SQLite error:", error)
        raise

    finally:
        close_con(con)


if __name__ == "__main__":
    load_data()