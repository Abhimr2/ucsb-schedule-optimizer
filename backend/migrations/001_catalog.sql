CREATE TABLE catalog_sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quarter TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    expected_total INTEGER CHECK (
        expected_total IS NULL OR expected_total >= 0
    ),
    fetched_total INTEGER NOT NULL CHECK (fetched_total >= 0),
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
    error_message TEXT
);

CREATE TABLE courses (
    quarter TEXT NOT NULL,
    course_id TEXT NOT NULL,
    raw_course_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    subject_area TEXT,
    department_code TEXT,
    college TEXT,
    level_code TEXT,
    contact_hours REAL,
    units_fixed REAL,
    units_variable_low REAL,
    units_variable_high REAL,
    grading_option TEXT,
    instruction_type TEXT,
    online INTEGER CHECK (online IS NULL OR online IN (0, 1)),
    source_order INTEGER NOT NULL,
    source_json TEXT NOT NULL,
    PRIMARY KEY (quarter, course_id)
);

CREATE TABLE course_general_education (
    quarter TEXT NOT NULL,
    course_id TEXT NOT NULL,
    ge_code TEXT NOT NULL,
    ge_college TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (quarter, course_id, ge_code, ge_college),
    FOREIGN KEY (quarter, course_id)
        REFERENCES courses (quarter, course_id)
        ON DELETE CASCADE
);

CREATE TABLE sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quarter TEXT NOT NULL,
    course_id TEXT NOT NULL,
    enroll_code TEXT NOT NULL,
    section_number TEXT NOT NULL,
    session TEXT,
    instruction_type_raw TEXT,
    instruction_type TEXT,
    class_closed INTEGER CHECK (
        class_closed IS NULL OR class_closed IN (0, 1)
    ),
    course_cancelled INTEGER CHECK (
        course_cancelled IS NULL OR course_cancelled IN (0, 1)
    ),
    grading_option_code TEXT,
    enrolled_total INTEGER,
    max_enroll INTEGER,
    secondary_status TEXT,
    department_approval_required INTEGER CHECK (
        department_approval_required IS NULL
        OR department_approval_required IN (0, 1)
    ),
    instructor_approval_required INTEGER CHECK (
        instructor_approval_required IS NULL
        OR instructor_approval_required IN (0, 1)
    ),
    restriction_level TEXT,
    restriction_major TEXT,
    restriction_major_pass TEXT,
    restriction_minor TEXT,
    restriction_minor_pass TEXT,
    source_order INTEGER NOT NULL,
    source_json TEXT NOT NULL,
    parent_section_id INTEGER,
    link_source TEXT,
    UNIQUE (quarter, enroll_code),
    FOREIGN KEY (quarter, course_id)
        REFERENCES courses (quarter, course_id)
        ON DELETE CASCADE,
    FOREIGN KEY (parent_section_id)
        REFERENCES sections (id)
        ON DELETE SET NULL
);

CREATE TABLE meetings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    days_raw TEXT,
    begin_time TEXT,
    end_time TEXT,
    start_minutes INTEGER,
    end_minutes INTEGER,
    building TEXT,
    room TEXT,
    room_capacity INTEGER,
    is_tba INTEGER NOT NULL DEFAULT 0 CHECK (is_tba IN (0, 1)),
    UNIQUE (section_id, ordinal),
    FOREIGN KEY (section_id)
        REFERENCES sections (id)
        ON DELETE CASCADE
);

CREATE TABLE meeting_days (
    meeting_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    day TEXT NOT NULL,
    PRIMARY KEY (meeting_id, day),
    UNIQUE (meeting_id, ordinal),
    FOREIGN KEY (meeting_id)
        REFERENCES meetings (id)
        ON DELETE CASCADE
);

CREATE TABLE section_instructors (
    section_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    name TEXT NOT NULL,
    function_code TEXT,
    PRIMARY KEY (section_id, ordinal),
    FOREIGN KEY (section_id)
        REFERENCES sections (id)
        ON DELETE CASCADE
);

CREATE TABLE section_concurrent_courses (
    section_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    value_json TEXT NOT NULL,
    PRIMARY KEY (section_id, ordinal),
    FOREIGN KEY (section_id)
        REFERENCES sections (id)
        ON DELETE CASCADE
);

CREATE INDEX idx_catalog_sync_runs_quarter
    ON catalog_sync_runs (quarter, id);

CREATE INDEX idx_courses_search_code
    ON courses (quarter, course_id COLLATE NOCASE);

CREATE INDEX idx_courses_search_title
    ON courses (quarter, title COLLATE NOCASE);

CREATE INDEX idx_courses_search_department
    ON courses (quarter, department_code COLLATE NOCASE);

CREATE INDEX idx_sections_course
    ON sections (quarter, course_id, source_order);

CREATE INDEX idx_sections_instruction_type
    ON sections (quarter, instruction_type);

CREATE INDEX idx_sections_parent
    ON sections (parent_section_id);

CREATE INDEX idx_meetings_section
    ON meetings (section_id, ordinal);

CREATE INDEX idx_meeting_days_day
    ON meeting_days (day, meeting_id);
