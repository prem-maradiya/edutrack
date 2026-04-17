from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
import mysql.connector
from mysql.connector import Error
import os
import time
from datetime import date, datetime
import logging

# ─────────────────────────────────────────────
# APP SETUP
# ─────────────────────────────────────────────
app = Flask(__name__)

# Secret key needed for flash messages (user feedback)
# Read from Docker secret file, fallback to env var for local dev
app.secret_key = os.environ.get("SECRET_KEY", "fallback-dev-secret-key-change-in-prod")

# ─────────────────────────────────────────────
# LOGGING SETUP
# Docker picks up logs from stdout/stderr automatically
# So we log to console — visible via: docker logs flask_app
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]  # stdout → Docker captures this
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# DATABASE CONNECTION FUNCTION
# Problem with old code: one global connection that crashes
# Fix: a function that creates a fresh connection every time
# and retries if MySQL isn't ready yet (important at startup)
# ─────────────────────────────────────────────
def read_secret(secret_name, fallback=None):
    """
    Reads a Docker secret from /run/secrets/<secret_name>
    Docker secrets are mounted as files inside the container.
    Falls back to environment variable if secret file doesn't exist.
    This way the app works both with Docker secrets AND plain env vars.
    """
    secret_path = f"/run/secrets/{secret_name}"
    try:
        with open(secret_path, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        # Fallback to environment variable (useful for local dev without secrets)
        logger.warning(f"⚠️ Secret file {secret_path} not found, falling back to env var.")
        return os.environ.get(fallback, "")


def get_db_connection():
    """
    Creates and returns a new MySQL connection.
    Retries up to 10 times with 5s delay (MySQL takes time to start).
    Password read from Docker secret file — never from plain env var.
    """
    # Read password from Docker secret, fallback to env var for local dev
    db_password = read_secret("db_password", fallback="DB_PASSWORD")

    for attempt in range(10):
        try:
            conn = mysql.connector.connect(
                host=os.environ.get("DB_HOST", "db"),
                user=os.environ.get("DB_USER", "edutrack_user"),
                password=db_password,
                database=os.environ.get("DB_NAME", "student_db"),
                connection_timeout=10
            )
            logger.info("✅ Connected to MySQL successfully.")
            return conn
        except Error as e:
            logger.warning(f"⏳ MySQL not ready (attempt {attempt + 1}/10): {e}")
            time.sleep(5)

    logger.error("❌ Could not connect to MySQL after 10 attempts.")
    raise Exception("Failed to connect to MySQL database.")


# ─────────────────────────────────────────────
# DATABASE INITIALIZATION
# Creates tables if they don't exist
# Runs once when app starts
# ─────────────────────────────────────────────
def init_db():
    """
    Creates the students and attendance tables if they don't exist.
    Called once at app startup.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Students table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            roll VARCHAR(50) NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Attendance table
    # UNIQUE constraint on (student_id, date) prevents duplicate attendance
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id INT AUTO_INCREMENT PRIMARY KEY,
            student_id INT NOT NULL,
            date DATE NOT NULL,
            status ENUM('Present', 'Absent') NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
            UNIQUE KEY unique_attendance (student_id, date)
        )
    """)

    conn.commit()
    cursor.close()
    conn.close()
    logger.info("✅ Database tables initialized.")


# ─────────────────────────────────────────────
# HELPER: Get attendance stats for a student
# Extracted as a function to avoid code repetition
# ─────────────────────────────────────────────
def get_student_stats(cursor, student_id):
    """Returns (total, present, percentage, status) for a student."""
    cursor.execute(
        "SELECT COUNT(*) FROM attendance WHERE student_id = %s",
        (student_id,)
    )
    total = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(*) FROM attendance WHERE student_id = %s AND status = 'Present'",
        (student_id,)
    )
    present = cursor.fetchone()[0]

    percentage = round((present / total) * 100, 2) if total > 0 else 0.0
    status = "Defaulter" if percentage < 75 else "OK"

    return total, present, percentage, status


# ─────────────────────────────────────────────
# ROUTES
# ─────────────────────────────────────────────

@app.route("/")
def index():
    """
    Home page — shows all students with attendance stats.
    Also calculates dashboard summary numbers.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, name, roll FROM students ORDER BY id")
    rows = cursor.fetchall()

    students = []
    for row in rows:
        student_id, name, roll = row
        total, present, percentage, status = get_student_stats(cursor, student_id)
        students.append({
            "id": student_id,
            "name": name,
            "roll": roll,
            "present": present,
            "total": total,
            "percentage": percentage,
            "status": status
        })

    # Dashboard stats — passed to template for analytics cards
    total_students = len(students)
    defaulters = sum(1 for s in students if s["status"] == "Defaulter")
    avg_attendance = round(
        sum(s["percentage"] for s in students) / total_students, 2
    ) if total_students > 0 else 0

    cursor.close()
    conn.close()

    logger.info(f"📋 Index loaded — {total_students} students")

    return render_template(
        "index.html",
        students=students,
        total_students=total_students,
        defaulters=defaulters,
        avg_attendance=avg_attendance
    )


@app.route("/add", methods=["POST"])
def add_student():
    """Adds a new student. Validates input before inserting."""
    name = request.form.get("name", "").strip()
    roll = request.form.get("roll", "").strip()

    # Input validation
    if not name or not roll:
        flash("❌ Name and Roll Number are required.", "error")
        return redirect(url_for("index"))

    if len(name) > 100 or len(roll) > 50:
        flash("❌ Name or Roll Number is too long.", "error")
        return redirect(url_for("index"))

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "INSERT INTO students (name, roll) VALUES (%s, %s)",
            (name, roll)
        )
        conn.commit()
        flash(f"✅ Student '{name}' added successfully!", "success")
        logger.info(f"➕ Added student: {name} | Roll: {roll}")
    except mysql.connector.IntegrityError:
        # Triggered when roll number already exists (UNIQUE constraint)
        flash(f"❌ Roll number '{roll}' already exists.", "error")
        logger.warning(f"⚠️ Duplicate roll: {roll}")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("index"))


@app.route("/edit/<int:id>")
def edit_student(id):
    """Shows edit form for a student."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, roll FROM students WHERE id = %s", (id,))
    student = cursor.fetchone()
    cursor.close()
    conn.close()

    if not student:
        flash("❌ Student not found.", "error")
        return redirect(url_for("index"))

    return render_template("edit.html", student=student)


@app.route("/update/<int:id>", methods=["POST"])
def update_student(id):
    """Updates student name and roll."""
    name = request.form.get("name", "").strip()
    roll = request.form.get("roll", "").strip()

    if not name or not roll:
        flash("❌ Name and Roll Number are required.", "error")
        return redirect(url_for("edit_student", id=id))

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            "UPDATE students SET name = %s, roll = %s WHERE id = %s",
            (name, roll, id)
        )
        conn.commit()
        flash(f"✅ Student updated successfully!", "success")
        logger.info(f"✏️ Updated student ID {id}: {name} | {roll}")
    except mysql.connector.IntegrityError:
        flash(f"❌ Roll number '{roll}' already exists.", "error")
    finally:
        cursor.close()
        conn.close()

    return redirect(url_for("index"))


@app.route("/delete/<int:id>")
def delete_student(id):
    """
    Deletes a student.
    Attendance is auto-deleted due to ON DELETE CASCADE in DB schema.
    No need to manually delete attendance first (unlike old code).
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT name FROM students WHERE id = %s", (id,))
    student = cursor.fetchone()

    if student:
        cursor.execute("DELETE FROM students WHERE id = %s", (id,))
        conn.commit()
        flash(f"✅ Student '{student[0]}' deleted.", "success")
        logger.info(f"🗑️ Deleted student ID {id}: {student[0]}")
    else:
        flash("❌ Student not found.", "error")

    cursor.close()
    conn.close()
    return redirect(url_for("index"))


@app.route("/attendance")
def attendance():
    """Shows attendance marking page with today's date."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, roll FROM students ORDER BY roll")
    students = cursor.fetchall()

    # Check if attendance already marked today
    today = date.today()
    cursor.execute(
        "SELECT COUNT(*) FROM attendance WHERE date = %s", (today,)
    )
    already_marked = cursor.fetchone()[0] > 0

    cursor.close()
    conn.close()

    return render_template(
        "attendance.html",
        students=students,
        today=today.strftime("%d %B %Y"),
        already_marked=already_marked
    )


@app.route("/mark_attendance", methods=["POST"])
def mark_attendance():
    """
    Marks attendance for all students.
    Uses INSERT ... ON DUPLICATE KEY UPDATE to handle re-submission safely.
    This prevents duplicate entries even if form is submitted twice.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    today = date.today()

    cursor.execute("SELECT id FROM students")
    students = cursor.fetchall()

    marked_count = 0
    for (student_id,) in students:
        status = request.form.get(f"status_{student_id}", "Absent")

        # ON DUPLICATE KEY UPDATE — safe re-submission
        # If attendance for this student+date already exists, it updates
        cursor.execute("""
            INSERT INTO attendance (student_id, date, status)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE status = VALUES(status)
        """, (student_id, today, status))
        marked_count += 1

    conn.commit()
    cursor.close()
    conn.close()

    flash(f"✅ Attendance marked for {marked_count} students.", "success")
    logger.info(f"📅 Attendance marked for {today} — {marked_count} students")
    return redirect(url_for("index"))


# ─────────────────────────────────────────────
# API ENDPOINT — for Chart.js dashboard
# Returns JSON data for the attendance chart
# ─────────────────────────────────────────────
@app.route("/api/attendance-data")
def attendance_data():
    """
    Returns JSON with student names + attendance percentages.
    Used by Chart.js on the frontend dashboard.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, name FROM students ORDER BY id")
    students = cursor.fetchall()

    labels = []
    percentages = []

    for student_id, name in students:
        _, _, percentage, _ = get_student_stats(cursor, student_id)
        labels.append(name)
        percentages.append(percentage)

    cursor.close()
    conn.close()

    return jsonify({"labels": labels, "percentages": percentages})


# ─────────────────────────────────────────────
# HEALTH CHECK ENDPOINT
# Used by Docker health check in docker-compose.yml
# Returns 200 OK if app is running
# ─────────────────────────────────────────────
@app.route("/health")
def health():
    """Docker health check endpoint."""
    try:
        conn = get_db_connection()
        conn.close()
        return jsonify({"status": "healthy", "database": "connected"}), 200
    except Exception:
        return jsonify({"status": "unhealthy", "database": "disconnected"}), 500


# ─────────────────────────────────────────────
# APP ENTRY POINT
# ─────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🚀 Starting Student Management System...")
    init_db()  # Create tables on startup
    app.run(host="0.0.0.0", port=5000, debug=False)