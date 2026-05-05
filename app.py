from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, make_response
import mysql.connector
from mysql.connector import Error
import os
import time
import csv
import io
from datetime import date, datetime
import logging

# ─────────────────────────────────────────────
# APP SETUP
# ─────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "edutrack-secret-2025")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# DOCKER SECRETS READER
# ─────────────────────────────────────────────
def read_secret(secret_name, fallback=None):
    secret_path = f"/run/secrets/{secret_name}"
    try:
        with open(secret_path, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return os.environ.get(fallback, "")


# ─────────────────────────────────────────────
# DATABASE CONNECTION
# ─────────────────────────────────────────────
def get_db_connection():
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
            return conn
        except Error as e:
            logger.warning(f"⏳ MySQL not ready (attempt {attempt + 1}/10): {e}")
            time.sleep(5)
    raise Exception("Failed to connect to MySQL database.")


# ─────────────────────────────────────────────
# DATABASE INITIALIZATION
# ─────────────────────────────────────────────
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS academic_years (
            id         INT AUTO_INCREMENT PRIMARY KEY,
            name       VARCHAR(20) NOT NULL UNIQUE,
            start_date DATE NOT NULL,
            end_date   DATE NOT NULL,
            is_active  BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS classes (
            id         INT AUTO_INCREMENT PRIMARY KEY,
            name       VARCHAR(50) NOT NULL,
            year_id    INT NOT NULL,
            threshold  INT DEFAULT 75,
            capacity   INT DEFAULT 60,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (year_id) REFERENCES academic_years(id) ON DELETE CASCADE,
            UNIQUE KEY unique_class_year (name, year_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            enrollment_id VARCHAR(20) NOT NULL UNIQUE,
            name          VARCHAR(100) NOT NULL,
            roll          VARCHAR(50) NOT NULL,
            class_id      INT NOT NULL,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE,
            UNIQUE KEY unique_roll_class (roll, class_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS holidays (
            id         INT AUTO_INCREMENT PRIMARY KEY,
            date       DATE NOT NULL UNIQUE,
            reason     VARCHAR(200) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance (
            id         INT AUTO_INCREMENT PRIMARY KEY,
            student_id INT NOT NULL,
            class_id   INT NOT NULL,
            date       DATE NOT NULL,
            status     ENUM('Present','Absent') NOT NULL,
            marked_by  VARCHAR(100) DEFAULT 'Faculty',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (student_id) REFERENCES students(id) ON DELETE CASCADE,
            FOREIGN KEY (class_id)   REFERENCES classes(id)  ON DELETE CASCADE,
            UNIQUE KEY unique_student_date (student_id, date)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS attendance_audit (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            attendance_id INT NOT NULL,
            student_id    INT NOT NULL,
            date          DATE NOT NULL,
            old_status    ENUM('Present','Absent') NOT NULL,
            new_status    ENUM('Present','Absent') NOT NULL,
            reason        VARCHAR(500) NOT NULL,
            changed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (attendance_id) REFERENCES attendance(id) ON DELETE CASCADE,
            FOREIGN KEY (student_id)    REFERENCES students(id)   ON DELETE CASCADE
        )
    """)

    # Seed default data
    cursor.execute("SELECT COUNT(*) FROM academic_years")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
            INSERT INTO academic_years (name, start_date, end_date, is_active)
            VALUES ('2025-26', '2025-06-01', '2026-05-31', TRUE)
        """)
        cursor.execute("""
            INSERT INTO classes (name, year_id, threshold, capacity) VALUES
            ('FY-A', 1, 75, 60), ('FY-B', 1, 75, 60),
            ('SY-A', 1, 75, 60), ('SY-B', 1, 75, 60),
            ('TY-A', 1, 75, 60), ('TY-B', 1, 75, 60)
        """)

    conn.commit()
    cursor.close()
    conn.close()
    logger.info("✅ Database initialized.")


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def generate_enrollment_id():
    conn = get_db_connection()
    cursor = conn.cursor()
    year = datetime.now().year
    prefix = f"MIT-{year}-"
    cursor.execute("""
        SELECT enrollment_id FROM students
        WHERE enrollment_id LIKE %s
        ORDER BY enrollment_id DESC LIMIT 1
    """, (f"{prefix}%",))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        last_num = int(row[0].split("-")[-1])
        return f"{prefix}{str(last_num + 1).zfill(3)}"
    return f"{prefix}001"


def get_student_stats(cursor, student_id, threshold=75):
    # Use a fresh connection with tuple cursor (not dictionary)
    # to avoid KeyError when called from dictionary=True cursors
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM attendance WHERE student_id = %s
        AND date NOT IN (SELECT date FROM holidays)
    """, (student_id,))
    total = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FROM attendance
        WHERE student_id = %s AND status = 'Present'
        AND date NOT IN (SELECT date FROM holidays)
    """, (student_id,))
    present = cur.fetchone()[0]
    cur.close()
    conn.close()

    absent = total - present
    percentage = round((present / total) * 100, 2) if total > 0 else 0.0
    status = "Defaulter" if percentage < threshold else "OK"
    return total, present, absent, percentage, status


# ═════════════════════════════════════════════
# DASHBOARD
# ═════════════════════════════════════════════
@app.route("/")
def dashboard():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT COUNT(*) as count FROM students")
    total_students = cursor.fetchone()["count"]

    cursor.execute("SELECT COUNT(*) as count FROM classes")
    total_classes = cursor.fetchone()["count"]

    today = date.today()
    cursor.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN status='Present' THEN 1 ELSE 0 END) as present
        FROM attendance WHERE date = %s
    """, (today,))
    today_att     = cursor.fetchone()
    today_total   = today_att["total"] or 0
    today_present = today_att["present"] or 0
    today_pct     = round((today_present / today_total) * 100, 1) if today_total > 0 else 0

    cursor.execute("SELECT id, class_id FROM students")
    students = cursor.fetchall()
    cursor.execute("SELECT id, threshold FROM classes")
    class_thresholds = {r["id"]: r["threshold"] for r in cursor.fetchall()}

    defaulters_count = 0
    for s in students:
        threshold = class_thresholds.get(s["class_id"], 75)
        total, _, _, pct, status = get_student_stats(cursor, s["id"], threshold)
        if status == "Defaulter" and total > 0:
            defaulters_count += 1

    cursor.execute("""
        SELECT date,
               COUNT(*) as total,
               SUM(CASE WHEN status='Present' THEN 1 ELSE 0 END) as present
        FROM attendance
        WHERE date >= DATE_SUB(%s, INTERVAL 7 DAY)
        GROUP BY date ORDER BY date ASC
    """, (today,))
    trend_rows    = cursor.fetchall()
    trend_labels  = [str(r["date"]) for r in trend_rows]
    trend_data    = [
        round((r["present"] / r["total"]) * 100, 1) if r["total"] > 0 else 0
        for r in trend_rows
    ]

    cursor.execute("""
        SELECT s.id, s.enrollment_id, s.name, s.roll,
               c.name as class_name, c.threshold
        FROM students s JOIN classes c ON s.class_id = c.id
        ORDER BY s.name
    """)
    all_students  = cursor.fetchall()
    defaulter_list = []
    for s in all_students:
        total, present, absent, pct, status = get_student_stats(cursor, s["id"], s["threshold"])
        if status == "Defaulter" and total > 0:
            defaulter_list.append({**s, "total": total, "present": present, "percentage": pct})

    cursor.close()
    conn.close()

    return render_template("dashboard.html",
        total_students=total_students,
        total_classes=total_classes,
        today_pct=today_pct,
        today_present=today_present,
        today_total=today_total,
        defaulters_count=defaulters_count,
        trend_labels=trend_labels,
        trend_data=trend_data,
        defaulter_list=defaulter_list[:5],
        today=today.strftime("%d %B %Y")
    )


# ═════════════════════════════════════════════
# STUDENTS
# ═════════════════════════════════════════════
@app.route("/students")
def students():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    search   = request.args.get("search", "").strip()
    class_id = request.args.get("class_id", "")

    query = """
        SELECT s.id, s.enrollment_id, s.name, s.roll,
               c.name as class_name, c.id as class_id, c.threshold
        FROM students s JOIN classes c ON s.class_id = c.id WHERE 1=1
    """
    params = []
    if search:
        query += " AND (s.name LIKE %s OR s.enrollment_id LIKE %s OR s.roll LIKE %s)"
        params += [f"%{search}%", f"%{search}%", f"%{search}%"]
    if class_id:
        query += " AND s.class_id = %s"
        params.append(class_id)
    query += " ORDER BY s.enrollment_id"

    cursor.execute(query, params)
    raw_students = cursor.fetchall()

    student_list = []
    for s in raw_students:
        total, present, absent, pct, status = get_student_stats(cursor, s["id"], s["threshold"])
        student_list.append({**s, "total": total, "present": present, "percentage": pct, "status": status})

    cursor.execute("SELECT id, name FROM classes ORDER BY name")
    classes = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template("students.html",
        students=student_list, classes=classes,
        search=search, selected_class=class_id
    )


@app.route("/students/add", methods=["POST"])
def add_student():
    name     = request.form.get("name", "").strip()
    roll     = request.form.get("roll", "").strip()
    class_id = request.form.get("class_id", "").strip()

    if not name or not roll or not class_id:
        flash("❌ All fields are required.", "error")
        return redirect(url_for("students"))

    enrollment_id = generate_enrollment_id()
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO students (enrollment_id, name, roll, class_id)
            VALUES (%s, %s, %s, %s)
        """, (enrollment_id, name, roll, class_id))
        conn.commit()
        flash(f"✅ Student '{name}' added — ID: {enrollment_id}", "success")
    except mysql.connector.IntegrityError:
        flash(f"❌ Roll '{roll}' already exists in this class.", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("students"))


@app.route("/students/edit/<int:id>")
def edit_student(id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM students WHERE id = %s", (id,))
    student = cursor.fetchone()
    cursor.execute("SELECT id, name FROM classes ORDER BY name")
    classes = cursor.fetchall()
    cursor.close()
    conn.close()
    if not student:
        flash("❌ Student not found.", "error")
        return redirect(url_for("students"))
    return render_template("edit_student.html", student=student, classes=classes)


@app.route("/students/update/<int:id>", methods=["POST"])
def update_student(id):
    name     = request.form.get("name", "").strip()
    roll     = request.form.get("roll", "").strip()
    class_id = request.form.get("class_id", "").strip()
    if not name or not roll or not class_id:
        flash("❌ All fields are required.", "error")
        return redirect(url_for("edit_student", id=id))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE students SET name=%s, roll=%s, class_id=%s WHERE id=%s",
                       (name, roll, class_id, id))
        conn.commit()
        flash("✅ Student updated.", "success")
    except mysql.connector.IntegrityError:
        flash(f"❌ Roll '{roll}' already exists in this class.", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("students"))


@app.route("/students/delete/<int:id>")
def delete_student(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM students WHERE id = %s", (id,))
    student = cursor.fetchone()
    if student:
        cursor.execute("DELETE FROM students WHERE id = %s", (id,))
        conn.commit()
        flash(f"✅ Student '{student[0]}' deleted.", "success")
    else:
        flash("❌ Student not found.", "error")
    cursor.close()
    conn.close()
    return redirect(url_for("students"))


# ═════════════════════════════════════════════
# CLASSES
# ═════════════════════════════════════════════
@app.route("/classes")
def classes():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT c.id, c.name, c.threshold, c.capacity,
               ay.name as year_name, COUNT(s.id) as student_count
        FROM classes c
        JOIN academic_years ay ON c.year_id = ay.id
        LEFT JOIN students s ON s.class_id = c.id
        GROUP BY c.id ORDER BY c.name
    """)
    class_list = cursor.fetchall()
    cursor.execute("SELECT id, name FROM academic_years ORDER BY name")
    years = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("classes.html", classes=class_list, years=years)


@app.route("/classes/add", methods=["POST"])
def add_class():
    name      = request.form.get("name", "").strip().upper()
    year_id   = request.form.get("year_id", "").strip()
    threshold = request.form.get("threshold", 75)
    capacity  = request.form.get("capacity", 60)
    if not name or not year_id:
        flash("❌ Class name and year are required.", "error")
        return redirect(url_for("classes"))
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO classes (name, year_id, threshold, capacity) VALUES (%s,%s,%s,%s)",
                       (name, year_id, threshold, capacity))
        conn.commit()
        flash(f"✅ Class '{name}' created.", "success")
    except mysql.connector.IntegrityError:
        flash(f"❌ Class '{name}' already exists for this year.", "error")
    finally:
        cursor.close()
        conn.close()
    return redirect(url_for("classes"))


@app.route("/classes/delete/<int:id>")
def delete_class(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM classes WHERE id = %s", (id,))
    cls = cursor.fetchone()
    if cls:
        cursor.execute("DELETE FROM classes WHERE id = %s", (id,))
        conn.commit()
        flash(f"✅ Class '{cls[0]}' deleted.", "success")
    else:
        flash("❌ Class not found.", "error")
    cursor.close()
    conn.close()
    return redirect(url_for("classes"))


# ═════════════════════════════════════════════
# ATTENDANCE
# ═════════════════════════════════════════════
@app.route("/attendance")
def attendance():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name FROM classes ORDER BY name")
    classes = cursor.fetchall()
    cursor.close()
    conn.close()
    today = date.today().strftime("%Y-%m-%d")
    return render_template("attendance.html", classes=classes, today=today)


@app.route("/attendance/students")
def get_attendance_students():
    class_id      = request.args.get("class_id")
    selected_date = request.args.get("date")
    if not class_id or not selected_date:
        return jsonify({"error": "Missing params"}), 400

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT s.id, s.name, s.roll, s.enrollment_id
        FROM students s WHERE s.class_id = %s ORDER BY s.roll
    """, (class_id,))
    students = cursor.fetchall()

    cursor.execute("""
        SELECT student_id, status FROM attendance
        WHERE class_id = %s AND date = %s
    """, (class_id, selected_date))
    existing = {row["student_id"]: row["status"] for row in cursor.fetchall()}

    cursor.execute("SELECT reason FROM holidays WHERE date = %s", (selected_date,))
    holiday = cursor.fetchone()

    cursor.close()
    conn.close()

    return jsonify({
        "students": students,
        "existing": existing,
        "already_marked": len(existing) > 0,
        "holiday": holiday["reason"] if holiday else None
    })


@app.route("/attendance/mark", methods=["POST"])
def mark_attendance():
    class_id      = request.form.get("class_id")
    selected_date = request.form.get("date")
    if not class_id or not selected_date:
        flash("❌ Class and date are required.", "error")
        return redirect(url_for("attendance"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM students WHERE class_id = %s", (class_id,))
    students = cursor.fetchall()

    marked = 0
    for s in students:
        status = request.form.get(f"status_{s['id']}", "Absent")
        cursor.execute("""
            INSERT INTO attendance (student_id, class_id, date, status)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE status = VALUES(status)
        """, (s["id"], class_id, selected_date, status))
        marked += 1

    conn.commit()
    cursor.close()
    conn.close()
    flash(f"✅ Attendance marked for {marked} students on {selected_date}.", "success")
    return redirect(url_for("attendance"))


# ═════════════════════════════════════════════
# HISTORY
# ═════════════════════════════════════════════
@app.route("/history")
def history():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    class_id  = request.args.get("class_id", "")
    from_date = request.args.get("from_date", "")
    to_date   = request.args.get("to_date", "")

    query = """
        SELECT a.id, a.date, a.status, a.updated_at,
               s.name as student_name, s.enrollment_id, s.roll,
               c.name as class_name
        FROM attendance a
        JOIN students s ON a.student_id = s.id
        JOIN classes c  ON a.class_id = c.id WHERE 1=1
    """
    params = []
    if class_id:
        query += " AND a.class_id = %s"
        params.append(class_id)
    if from_date:
        query += " AND a.date >= %s"
        params.append(from_date)
    if to_date:
        query += " AND a.date <= %s"
        params.append(to_date)
    query += " ORDER BY a.date DESC, s.roll ASC LIMIT 200"

    cursor.execute(query, params)
    records = cursor.fetchall()

    cursor.execute("SELECT id, name FROM classes ORDER BY name")
    classes = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template("history.html",
        records=records, classes=classes,
        class_id=class_id, from_date=from_date, to_date=to_date
    )


@app.route("/history/edit/<int:id>", methods=["POST"])
def edit_attendance(id):
    new_status = request.form.get("status")
    reason     = request.form.get("reason", "").strip()

    if not new_status or not reason:
        flash("❌ Status and reason are both required.", "error")
        return redirect(url_for("history"))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM attendance WHERE id = %s", (id,))
    record = cursor.fetchone()

    if not record:
        flash("❌ Record not found.", "error")
        cursor.close()
        conn.close()
        return redirect(url_for("history"))

    old_status = record["status"]
    if old_status == new_status:
        flash("ℹ️ No change made — status is the same.", "error")
        cursor.close()
        conn.close()
        return redirect(url_for("history"))

    cursor.execute("UPDATE attendance SET status = %s WHERE id = %s", (new_status, id))
    cursor.execute("""
        INSERT INTO attendance_audit
        (attendance_id, student_id, date, old_status, new_status, reason)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (id, record["student_id"], record["date"], old_status, new_status, reason))

    conn.commit()
    cursor.close()
    conn.close()
    flash(f"✅ Updated: {old_status} → {new_status}.", "success")
    return redirect(url_for("history"))


# ═════════════════════════════════════════════
# DEFAULTERS
# ═════════════════════════════════════════════
@app.route("/defaulters")
def defaulters():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    class_id = request.args.get("class_id", "")
    query = """
        SELECT s.id, s.enrollment_id, s.name, s.roll,
               c.name as class_name, c.threshold, c.id as class_id
        FROM students s JOIN classes c ON s.class_id = c.id WHERE 1=1
    """
    params = []
    if class_id:
        query += " AND s.class_id = %s"
        params.append(class_id)
    query += " ORDER BY s.name"

    cursor.execute(query, params)
    all_students = cursor.fetchall()

    defaulter_list = []
    for s in all_students:
        total, present, absent, pct, status = get_student_stats(cursor, s["id"], s["threshold"])
        if status == "Defaulter" and total > 0:
            defaulter_list.append({**s, "total": total, "present": present, "absent": absent, "percentage": pct})

    cursor.execute("SELECT id, name FROM classes ORDER BY name")
    classes = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template("defaulters.html",
        defaulters=defaulter_list, classes=classes, selected_class=class_id
    )


# ═════════════════════════════════════════════
# REPORTS
# ═════════════════════════════════════════════
@app.route("/reports")
def reports():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name FROM classes ORDER BY name")
    classes = cursor.fetchall()
    cursor.close()
    conn.close()
    return render_template("reports.html", classes=classes)


@app.route("/reports/generate")
def generate_report():
    class_id  = request.args.get("class_id", "")
    from_date = request.args.get("from_date", "")
    to_date   = request.args.get("to_date", "")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT s.enrollment_id, s.name, s.roll,
               c.name as class_name, c.threshold,
               COUNT(a.id) as total,
               SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END) as present,
               SUM(CASE WHEN a.status='Absent'  THEN 1 ELSE 0 END) as absent
        FROM students s
        JOIN classes c ON s.class_id = c.id
        LEFT JOIN attendance a ON a.student_id = s.id
    """
    params = []
    conditions = []
    if class_id:
        conditions.append("s.class_id = %s")
        params.append(class_id)
    if from_date:
        conditions.append("(a.date IS NULL OR a.date >= %s)")
        params.append(from_date)
    if to_date:
        conditions.append("(a.date IS NULL OR a.date <= %s)")
        params.append(to_date)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " GROUP BY s.id ORDER BY c.name, s.roll"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    report_data = []
    for row in rows:
        total   = row["total"] or 0
        present = row["present"] or 0
        pct     = round((present / total) * 100, 2) if total > 0 else 0
        status  = "Defaulter" if pct < row["threshold"] and total > 0 else "OK"
        report_data.append({**row, "percentage": pct, "status": status})

    return jsonify(report_data)


@app.route("/reports/export/csv")
def export_csv():
    class_id  = request.args.get("class_id", "")
    from_date = request.args.get("from_date", "")
    to_date   = request.args.get("to_date", "")

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT s.enrollment_id, s.name, s.roll,
               c.name as class_name, c.threshold,
               COUNT(a.id) as total,
               SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END) as present,
               SUM(CASE WHEN a.status='Absent'  THEN 1 ELSE 0 END) as absent
        FROM students s
        JOIN classes c ON s.class_id = c.id
        LEFT JOIN attendance a ON a.student_id = s.id
    """
    params = []
    conditions = []
    if class_id:
        conditions.append("s.class_id = %s")
        params.append(class_id)
    if from_date:
        conditions.append("(a.date IS NULL OR a.date >= %s)")
        params.append(from_date)
    if to_date:
        conditions.append("(a.date IS NULL OR a.date <= %s)")
        params.append(to_date)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " GROUP BY s.id ORDER BY c.name, s.roll"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Enrollment ID", "Name", "Roll", "Class", "Total", "Present", "Absent", "Percentage", "Status"])

    for row in rows:
        total   = row["total"] or 0
        present = row["present"] or 0
        pct     = round((present / total) * 100, 2) if total > 0 else 0
        status  = "Defaulter" if pct < row["threshold"] and total > 0 else "OK"
        writer.writerow([row["enrollment_id"], row["name"], row["roll"],
                         row["class_name"], total, present, row["absent"] or 0,
                         f"{pct}%", status])

    output.seek(0)
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=attendance_report.csv"
    response.headers["Content-Type"] = "text/csv"
    return response


# ═════════════════════════════════════════════
# API + HEALTH
# ═════════════════════════════════════════════
@app.route("/api/attendance-data")
def attendance_data():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT s.name,
               COUNT(a.id) as total,
               SUM(CASE WHEN a.status='Present' THEN 1 ELSE 0 END) as present
        FROM students s
        LEFT JOIN attendance a ON a.student_id = s.id
        GROUP BY s.id ORDER BY s.name
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    labels = [r["name"] for r in rows]
    percentages = [
        round((r["present"] / r["total"]) * 100, 1) if r["total"] > 0 else 0
        for r in rows
    ]
    return jsonify({"labels": labels, "percentages": percentages})


# ═════════════════════════════════════════════
# BULK CSV IMPORT
# ═════════════════════════════════════════════
@app.route("/students/import", methods=["GET", "POST"])
def import_students():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id, name FROM classes ORDER BY name")
    classes = cursor.fetchall()
    cursor.close()
    conn.close()

    if request.method == "GET":
        return render_template("import_students.html", classes=classes)

    # POST — process uploaded CSV
    class_id = request.form.get("class_id", "").strip()
    file     = request.files.get("csv_file")

    if not class_id:
        flash("❌ Please select a class.", "error")
        return render_template("import_students.html", classes=classes)

    if not file or file.filename == "":
        flash("❌ Please upload a CSV file.", "error")
        return render_template("import_students.html", classes=classes)

    if not file.filename.endswith(".csv"):
        flash("❌ Only .csv files are accepted.", "error")
        return render_template("import_students.html", classes=classes)

    # Parse CSV
    stream  = io.StringIO(file.stream.read().decode("utf-8-sig"), newline=None)
    reader  = csv.DictReader(stream)

    # Validate headers
    required_headers = {"name", "roll"}
    if not required_headers.issubset(set(reader.fieldnames or [])):
        flash("❌ CSV must have columns: name, roll", "error")
        return render_template("import_students.html", classes=classes)

    conn   = get_db_connection()
    cursor = conn.cursor()

    success = 0
    skipped = 0
    errors  = []

    for i, row in enumerate(reader, start=2):  # start=2 because row 1 is header
        name = row.get("name", "").strip()
        roll = row.get("roll", "").strip()

        if not name or not roll:
            errors.append(f"Row {i}: Empty name or roll — skipped.")
            skipped += 1
            continue

        enrollment_id = generate_enrollment_id()
        try:
            cursor.execute("""
                INSERT INTO students (enrollment_id, name, roll, class_id)
                VALUES (%s, %s, %s, %s)
            """, (enrollment_id, name, roll, class_id))
            conn.commit()
            success += 1
        except mysql.connector.IntegrityError:
            errors.append(f"Row {i}: Roll '{roll}' already exists — skipped.")
            skipped += 1

    cursor.close()
    conn.close()

    if success:
        flash(f"✅ Imported {success} students successfully. {skipped} skipped.", "success")
    else:
        flash(f"❌ No students imported. {skipped} rows skipped.", "error")

    if errors:
        for err in errors[:5]:  # Show max 5 errors
            flash(f"⚠️ {err}", "error")

    logger.info(f"📥 CSV import: {success} added, {skipped} skipped")
    return redirect(url_for("students"))


@app.route("/students/download-template")
def download_template():
    """Download a sample CSV template for bulk import"""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["name", "roll"])
    writer.writerow(["Rahul Sharma", "23CSE001"])
    writer.writerow(["Priya Patel", "23CSE002"])
    writer.writerow(["Arjun Mehta", "23CSE003"])
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=students_template.csv"
    response.headers["Content-Type"] = "text/csv"
    return response


@app.route("/health")
def health():
    try:
        conn = get_db_connection()
        conn.close()
        return jsonify({"status": "healthy", "database": "connected"}), 200
    except Exception:
        return jsonify({"status": "unhealthy", "database": "disconnected"}), 500


# ═════════════════════════════════════════════
# STARTUP
# ═════════════════════════════════════════════
logger.info("🚀 Starting EduTrack v2.0...")
init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)