-- ─────────────────────────────────────────────
-- EduTrack Database Schema
-- Version: 2.0
-- ─────────────────────────────────────────────

USE student_db;

-- Grant permissions to app user
GRANT ALL PRIVILEGES ON student_db.* TO 'edutrack_user'@'%';
FLUSH PRIVILEGES;

-- ─────────────────────────────────────────────
-- TABLE 1: academic_years
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS academic_years (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(20) NOT NULL UNIQUE,
    start_date  DATE NOT NULL,
    end_date    DATE NOT NULL,
    is_active   BOOLEAN DEFAULT FALSE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────
-- TABLE 2: classes
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS classes (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(50) NOT NULL,
    year_id     INT NOT NULL,
    threshold   INT DEFAULT 75,
    capacity    INT DEFAULT 60,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (year_id) REFERENCES academic_years(id) ON DELETE CASCADE,
    UNIQUE KEY unique_class_year (name, year_id)
);

-- ─────────────────────────────────────────────
-- TABLE 3: students
-- enrollment_id format: MIT-YYYY-XXX
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS students (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    enrollment_id   VARCHAR(20) NOT NULL UNIQUE,
    name            VARCHAR(100) NOT NULL,
    roll            VARCHAR(50) NOT NULL,
    class_id        INT NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE,
    UNIQUE KEY unique_roll_class (roll, class_id)
);

-- ─────────────────────────────────────────────
-- TABLE 4: holidays
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS holidays (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    date        DATE NOT NULL UNIQUE,
    reason      VARCHAR(200) NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ─────────────────────────────────────────────
-- TABLE 5: attendance
-- UNIQUE(student_id, date) prevents duplicates
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS attendance (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    student_id  INT NOT NULL,
    class_id    INT NOT NULL,
    date        DATE NOT NULL,
    status      ENUM('Present', 'Absent') NOT NULL,
    marked_by   VARCHAR(100) DEFAULT 'Faculty',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
    FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE,
    UNIQUE KEY unique_student_date (student_id, date)
);

-- ─────────────────────────────────────────────
-- TABLE 6: attendance_audit
-- Logs every edit: old → new status + reason
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS attendance_audit (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    attendance_id   INT NOT NULL,
    student_id      INT NOT NULL,
    date            DATE NOT NULL,
    old_status      ENUM('Present', 'Absent') NOT NULL,
    new_status      ENUM('Present', 'Absent') NOT NULL,
    reason          VARCHAR(500) NOT NULL,
    changed_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (attendance_id) REFERENCES attendance(id) ON DELETE CASCADE,
    FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE
);

-- ─────────────────────────────────────────────
-- SEED DATA
-- ─────────────────────────────────────────────
INSERT IGNORE INTO academic_years (name, start_date, end_date, is_active)
VALUES ('2025-26', '2025-06-01', '2026-05-31', TRUE);

INSERT IGNORE INTO classes (name, year_id, threshold, capacity)
VALUES
    ('FY-A', 1, 75, 60),
    ('FY-B', 1, 75, 60),
    ('SY-A', 1, 75, 60),
    ('SY-B', 1, 75, 60),
    ('TY-A', 1, 75, 60),
    ('TY-B', 1, 75, 60);