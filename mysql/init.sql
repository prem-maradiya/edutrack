-- ─────────────────────────────────────────────
-- MySQL Initialization Script
-- Runs automatically when MySQL container starts for first time
-- Mounted via: ./mysql/init.sql:/docker-entrypoint-initdb.d/init.sql
-- ─────────────────────────────────────────────

-- Use the database created by docker-compose environment variables
USE student_db;

-- Grant permissions to edutrack_user
GRANT ALL PRIVILEGES ON student_db.* TO 'edutrack_user'@'%';
FLUSH PRIVILEGES;