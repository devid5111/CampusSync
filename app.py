"""
================================================================================
CAMPUSSYNC ERP - MAIN APPLICATION CONTROLLER & BOOTSTRAPPER (app.py)
================================================================================
આ ફાઇલ આખા CampusSync પ્રોજેક્ટનું મુખ્ય એન્ટ્રી પોઇન્ટ (Main Entry Point) છે.
અહીં નીચે મુજબની મહત્વની બાબતો કોન્ફિગર કરવામાં આવી છે:

1. Flask App Initialization & Middleware:
   - Werkzeug ProxyFix (Railway/Cloudflare રિવર્સ પ્રોક્સી પર સાચા Client IP અને HTTPS માટે)
   - Flask-Compress (HTML/CSS/JS/JSON નું ઓટોમેટેડ Gzip/Brotli કમ્પ્રેશન)
   
2. સિક્યુરિટી પોલિસી (Security Policies):
   - Enforce HTTPS (પ્રોડક્શનમાં http ને https પર રીડાયરેક્ટ કરે છે)
   - 30-Minute Inactivity Auto-Logout (30 મિનિટ કંઈ કામ ન થાય તો સુરક્ષા માટે ઓટો લોગઆઉટ)
   - Cache-Control Headers (લોગઆઉટ પછી કોઈ 'Back' બટન દબાવીને ડેશબોર્ડ ન જોઈ શકે)

3. બ્લૂપ્રિન્ટ્સ (Modular Route Blueprints):
   - auth_bp    : Login / Logout / Forgot Password (Admin, Faculty, Student)
   - admin_bp   : Admin Dashboard, Faculty/Student Management, Results, Reports
   - faculty_bp : Faculty Attendance, QR Code Generation, Marks Entry, Notices
   - student_bp : Student Dashboard, QR Attendance Scanning, Results, Notices
   - alumni_bp  : Old / Graduated Students Directory & Archives
   - api_bp     : Mobile REST API & Live Dynamic Notification Bell Feed

4. ઓટોમેટેડ ડેટાબેઝ માઇગ્રેશન (run_all_database_migrations):
   - સર્વર અથવા લોકલ જ્યારે પણ રન થાય ત્યારે ટેબલ્સ અને જરૂરી કોલમ્સ આપમેળે બની જાય છે.

5. File Serving & Utility Routes:
   - /uploads/* universal file handler (ફોટા અને દસ્તાવેજો માટે)
   - /test-email (Brevo API દ્વારા ઈમેલ ડિલિવરી ચેક કરવા માટે)
   - Error Handlers (404 Not Found, 500 Server Error, 403 Forbidden)
================================================================================
"""

from flask import Flask, send_from_directory, session, request, redirect, url_for, flash
import os
import traceback
from datetime import datetime
from sqlalchemy import inspect

# કન્ફિગરેશન અને એક્સટેન્શન્સ (Database & Mail Objects)
from config import Config
from extensions import db, mail
from utils.db import test_connection

# તમામ રોલ-વાઇઝ મોડ્યુલર બ્લૂપ્રિન્ટ્સ (Route Handlers)
from routes.auth import auth_bp
from routes.admin import admin_bp
from routes.faculty import faculty_bp
from routes.student import student_bp
from routes.alumni import alumni_bp
from routes.api import api_bp

# ------------------------------------------------------------------------------
# 1. FLASK APPLICATION INITIALIZATION
# ------------------------------------------------------------------------------
app = Flask(__name__)

# [ProxyFix Middleware]: Railway / Cloudflare / Nginx રિવર્સ પ્રોક્સી હેઠળ જ્યારે
# પ્રોજેક્ટ લાઈવ ચાલે, ત્યારે યુઝરનો સાચો Client IP અને HTTPS પ્રોટોકોલ
# X-Forwarded-* હેડર્સ દ્વારા એક્યુરેટ રીતે ઓળખવા માટે આ જરૂરી છે.
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1,
    x_proto=1,
    x_host=1,
    x_prefix=1
)

# .env ફાઇલમાંથી સિક્રેટ કી અને ડેટાબેઝ ક્રેડેન્શિયલ્સ લોડ કરે છે
app.config.from_object(Config)
db.init_app(app)
mail.init_app(app)

# [High-Performance Compression]: પેજ લોડિંગ સ્પીડ વધારવા માટે HTML, CSS, JS, JSON
# ડેટાને બ્રાઉઝર પર મોકલતા પહેલાં Gzip/Brotli દ્વારા કમ્પ્રેસ કરે છે.
try:
    from flask_compress import Compress
    Compress(app)
except ImportError:
    pass

# ------------------------------------------------------------------------------
# 2. SECURITY: ENFORCE HTTPS IN PRODUCTION DEPLOYMENTS
# ------------------------------------------------------------------------------
@app.before_request
def enforce_https():
    """
    પ્રોડક્શન સર્વર (દા.ત. Railway) પર સુરક્ષા માટે પ્લેન HTTP રિકવેસ્ટને
    આપમેળે HTTPS (SSL Secure) પર રીડાયરેક્ટ કરે છે.
    લોકલ ડેવલપમેન્ટ (localhost / 127.0.0.1) માં આ ડિસેબલ રહે છે જેથી એરર ન આવે.
    """
    host = request.host.split(':')[0].lower()
    if host in ('localhost', '127.0.0.1', '0.0.0.0') or host.startswith('192.168.') or host.startswith('10.') or host.endswith('.local'):
        return

    proto = request.headers.get('X-Forwarded-Proto', request.scheme)
    if proto == 'http':
        url = request.url.replace('http://', 'https://', 1)
        return redirect(url, code=301)


# ------------------------------------------------------------------------------
# 3. SECURITY: 30-MINUTE SESSION INACTIVITY TIMEOUT
# ------------------------------------------------------------------------------
@app.before_request
def enforce_session_inactivity_timeout():
    """
    સેશન ઇનએક્ટિવિટી સિક્યુરિટી:
    જો કોઈ યુઝર (Admin, Faculty, Student) પોર્ટલ ખોલીને 30 મિનિટ સુધી
    કોઈ એક્ટિવિટી ન કરે, તો સિક્યુરિટી ખાતર સેશન ક્લીયર કરીને તેને
    આપમેળે લોગિન પેજ પર મોકલી દે છે.
    """
    # સ્ટેટિક ફાઇલ્સ અને અપલોડેડ ઇમેજીસ માટે ટાઇમઆઉટ ચેક સ્કીપ કરવો
    if request.endpoint == 'static' or request.path.startswith('/static') or request.path.startswith('/assets') or request.path.startswith('/uploads'):
        return

    is_admin = 'admin_id' in session
    is_faculty = 'faculty_id' in session
    is_student = 'student_id' in session

    # જો કોઈ યુઝર લોગિન જ ન હોય તો બહાર નીકળી જવું
    if not (is_admin or is_faculty or is_student):
        return

    now_ts = datetime.utcnow().timestamp()
    last_active = session.get('last_active')
    TIMEOUT_SECONDS = 1800  # 30 મિનિટ = 1800 સેકન્ડ

    # જો 30 મિનિટથી વધુ સમય થઈ ગયો હોય તો સેશન ક્લીયર કરી લોગઆઉટ કરો
    if last_active and (now_ts - last_active > TIMEOUT_SECONDS):
        target_login = 'student_login'
        if is_admin:
            target_login = 'admin_login'
        elif is_faculty:
            target_login = 'faculty_login'

        session.clear()
        flash("Your session has expired due to 30 minutes of inactivity. Please log in again.", "warning")
        return redirect(url_for(target_login, expired='1'))

    # યુઝર સક્રિય છે, તેથી છેલ્લી એક્ટિવિટીનો સમય અપડેટ કરો
    session['last_active'] = now_ts
    session.permanent = True


# ------------------------------------------------------------------------------
# 4. SECURITY & SPEED: ANTI-BACK-BUTTON CACHE-CONTROL HEADERS
# ------------------------------------------------------------------------------
@app.after_request
def add_cache_control_headers(response):
    """
    એન્ટી-બેક-બટન પ્રોટેક્શન:
    ડાયનેમિક ડેશબોર્ડ પેજીસ માટે no-cache હેડર્સ સેટ કરે છે જેથી યુઝર લોગઆઉટ
    થયા પછી જો બ્રાઉઝરનું 'Back' બટન દબાવે તો કેશ્ડ પેજ ન દેખાય અને સર્વર
    તેને સીધું લોગિન પેજ પર રીડાયરેક્ટ કરે.
    
    તેની સામે Static CSS/JS અને Uploads માટે 24-Hour Cache રાખે છે જેથી સાઇટ સુપર-ફાસ્ટ લોડ થાય.
    """
    if request.path.startswith('/static') or request.path.startswith('/assets') or request.path.startswith('/uploads'):
        response.headers["Cache-Control"] = "public, max-age=86400"
        return response

    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ------------------------------------------------------------------------------
# 5. GLOBAL CONTEXT PROCESSOR (COLLEGE SETTINGS INJECTION)
# ------------------------------------------------------------------------------
from services.college_service import get_college_settings

@app.context_processor
def inject_global_college():
    """
    ગ્લોબલ કોન્ટેક્સ્ટ પ્રોસેસર:
    કોલેજનું નામ, લોગો, એડ્રેસ, ઈમેલ વગેરે ડેટાબેઝમાંથી લાવીને આખા પ્રોજેક્ટના
    દરેક HTML ટેમ્પલેટમાં ઓટોમેટિક ઉપલબ્ધ બનાવે છે (જેથી વારંવાર ક્વેરી ન કરવી પડે).
    """
    try:
        college = get_college_settings()
    except Exception:
        college = None
    return dict(college=college)


# ------------------------------------------------------------------------------
# 6. BLUEPRINT REGISTRATION
# ------------------------------------------------------------------------------
# આખા પ્રોજેક્ટના જુદા જુદા મોડ્યુલ્સને Flask App સાથે રજીસ્ટર કરીએ છીએ:
app.register_blueprint(auth_bp)      # લોગિન, પાસવર્ડ રીસેટ, ઓટીપી
app.register_blueprint(admin_bp)     # પ્રિન્સિપાલ / એડમિન મેનેજમેન્ટ
app.register_blueprint(faculty_bp)   # ફેકલ્ટી હાજરી, માર્ક્સ અને લેક્ચર
app.register_blueprint(student_bp)   # સ્ટુડન્ટ પોર્ટલ, રિઝલ્ટ, ક્યુઆર સ્કેન
app.register_blueprint(alumni_bp)    # પાસ થયેલા ઓલ્ડ સ્ટુડન્ટ્સ ડિરેક્ટરી
app.register_blueprint(api_bp)       # મોબાઇલ એપ્લિકેશન અને નોટિફિકેશન API

# જુના ટેમ્પલેટ્સમાં રહેલા શોર્ટ એન્ડપોઇન્ટ નામો (દા.ત. url_for('admin_login'))
# સાથે બેકવર્ડ કમ્પેટિબિલિટી જાળવી રાખવા માટે એલિયાસ રજીસ્ટ્રેશન લૂપ:
for rule in list(app.url_map.iter_rules()):
    if '.' in rule.endpoint:
        short_ep = rule.endpoint.split('.', 1)[1]
        if short_ep not in app.view_functions:
            app.view_functions[short_ep] = app.view_functions[rule.endpoint]
            app.add_url_rule(rule.rule, endpoint=short_ep, view_func=app.view_functions[rule.endpoint], methods=rule.methods)


# ------------------------------------------------------------------------------
# 7. AUTOMATED RESILIENT DATABASE MIGRATION ENGINE
# ------------------------------------------------------------------------------
def run_all_database_migrations():
    """
    ઓટોમેટેડ ડેટાબેઝ સ્કીમા માઇગ્રેશન એન્જિન:
    પ્રોજેક્ટ જ્યારે પણ સ્ટાર્ટ થાય (Railway સર્વર કે લોકલ PC પર), ત્યારે
    દરેક ટેબલ અને નવી કોલમ્સ ચેક કરીને આપમેળે બનાવી દે છે.
    દરેક બ્લોક આઈસોલેટેડ try-except માં છે જેથી એકમાં એરર આવે તો પણ બાકીના
    ટેબલ્સ અટક્યા વગર સહેલાઈથી માઈગ્રેટ થઈ જાય.
    """
    import models
    from models import (
        Admin, Student, CollegeSetting, AcademicSetting, Subject, EmailLog,
        Faculty, FacultySubjectAssignment, InternalMark, AttendanceRecord,
        ArchivedStudent, ArchivedInternalMark, ArchivedStudentOTP, Notification, NotificationRead,
        AssignmentSubmission, GalleryItem, HomePageSetting, ResultDeclaration,
        LectureAttendanceSession, LectureAttendanceStudent, AttendanceSecurityAlert
    )

    # સર્વર પર જરૂરી તમામ અપલોડ ફોલ્ડર્સ (Upload Folders) આપમેળે ક્રિએટ કરે છે
    upload_dirs = [
        'admin', 'assignments', 'college', 'excel', 'faculty',
        'gallery', 'homepage', 'notices', 'notifications', 'students', 'student'
    ]
    for sub in upload_dirs:
        os.makedirs(os.path.join(app.root_path, 'uploads', sub), exist_ok=True)

    report = {"migrated": [], "errors": []}

    # SQLAlchemy મોડેલ્સ મુજબ ટેબલ્સ બનાવે છે
    try:
        db.create_all()
    except Exception as e:
        report["errors"].append(f"create_all: {e}")

    try:
        db.engine.dispose()
    except Exception:
        pass

    # [1. નોટિફિકેશન્સ અને અનરેડ ટ્રેકિંગ ટેબલ (Notifications & Reads)]:
    try:
        from services.notification_service import ensure_notification_columns, ensure_notification_reads_table
        ensure_notification_columns()
        ensure_notification_reads_table()
        report["migrated"].append("notifications_and_reads")
    except Exception as e:
        report["errors"].append(f"ensure_notification_columns: {e}")

    # [2. ક્યુઆર સિક્યુરિટી એલર્ટ્સ ટેબલ (Anti-Proxy / Geofence Security Alerts)]:
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text("""
                CREATE TABLE IF NOT EXISTS attendance_security_alerts (
                    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    session_id INT NOT NULL,
                    student_id INT NOT NULL,
                    attempted_roll VARCHAR(20) NULL,
                    device_fingerprint VARCHAR(500) NULL,
                    conflicting_student_id INT NULL,
                    alert_type ENUM('DUPLICATE_DEVICE','OUT_OF_GEOFENCE','EXPIRED_TOKEN','UNBOUND_DEVICE') NOT NULL,
                    alert_message TEXT NULL,
                    scan_latitude DECIMAL(10,8) NULL,
                    scan_longitude DECIMAL(11,8) NULL,
                    distance_meters FLOAT NULL,
                    faculty_action ENUM('PENDING','APPROVED','REJECTED') DEFAULT 'PENDING',
                    resolved_by_faculty_id INT NULL,
                    created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
            """))
            conn.commit()
            report["migrated"].append("attendance_security_alerts")
    except Exception as e:
        report["errors"].append(f"attendance_security_alerts: {e}")

    # [3. સ્ટુડન્ટ્સ ટેબલ અને ડિવાઇસ બાઇન્ડિંગ કોલમ્સ (Students & Device Fingerprint)]:
    try:
        with db.engine.connect() as conn:
            s_existing = set()
            try:
                res = conn.execute(db.text("SHOW COLUMNS FROM students")).fetchall()
                s_existing = {str(r[0]).lower() for r in res}
            except Exception:
                pass

            student_cols = [
                ('academic_year', 'VARCHAR(20) NULL'),
                ('device_fingerprint', 'VARCHAR(500) NULL'),
                ('device_model', 'VARCHAR(100) NULL'),
                ('device_bound_at', 'DATETIME NULL'),
                ('device_reset_allowed', 'SMALLINT DEFAULT 0')
            ]
            for col_name, col_def in student_cols:
                if col_name.lower() not in s_existing:
                    try:
                        conn.execute(db.text(f"ALTER TABLE students ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
                        s_existing.add(col_name.lower())
                        report["migrated"].append(f"students.{col_name}")
                    except Exception as err:
                        report["errors"].append(f"students.{col_name}: {err}")

            # યુનિક ઇન્ડેક્સ સુનિશ્ચિત કરે છે કે એક સેમેસ્ટર અને શૈક્ષણિક વર્ષમાં એક રોલ નંબર એક જ વાર આવે
            try:
                res_idx = conn.execute(db.text("SHOW INDEX FROM students")).fetchall()
                idx_names = {str(r[2]) for r in res_idx if len(r) > 2}
            except Exception:
                idx_names = set()

            if 'roll_number' in idx_names:
                try:
                    conn.execute(db.text("ALTER TABLE students DROP INDEX roll_number"))
                    conn.commit()
                except Exception:
                    pass

            if 'uq_academic_course_roll' in idx_names:
                try:
                    conn.execute(db.text("ALTER TABLE students DROP INDEX uq_academic_course_roll"))
                    conn.commit()
                except Exception:
                    pass

            if 'uq_academic_course_sem_roll' not in idx_names:
                try:
                    conn.execute(db.text("ALTER TABLE students ADD CONSTRAINT uq_academic_course_sem_roll UNIQUE (academic_year, course, semester, roll_number)"))
                    conn.commit()
                except Exception:
                    pass
    except Exception as e:
        report["errors"].append(f"students_table: {e}")

    # [4. ડિફોલ્ટ એડમિન એકાઉન્ટ ઓટો-સીડ (Default Administrator Seed)]:
    try:
        if Admin.query.first() is None:
            from werkzeug.security import generate_password_hash
            default_admin = Admin(
                username='admin',
                password=generate_password_hash('admin'),
                full_name='System Administrator',
                email='admin@campussync.edu',
                status='Active'
            )
            db.session.add(default_admin)
            db.session.commit()
            report["migrated"].append("default_admin_seeded")
    except Exception as e:
        db.session.rollback()
        report["errors"].append(f"admin_autoseed: {e}")

    # [5. શૈક્ષણિક સેટિંગ્સ (Academic Settings - Students per Division)]:
    try:
        with db.engine.connect() as conn:
            existing = set()
            try:
                res = conn.execute(db.text("SHOW COLUMNS FROM academic_settings")).fetchall()
                existing = {str(r[0]).lower() for r in res}
            except Exception:
                pass
            if 'students_per_division' not in existing:
                try:
                    conn.execute(db.text("ALTER TABLE academic_settings ADD COLUMN students_per_division INT NOT NULL DEFAULT 70"))
                    conn.commit()
                    report["migrated"].append("academic_settings.students_per_division")
                except Exception as err:
                    report["errors"].append(f"academic_settings: {err}")
    except Exception as e:
        report["errors"].append(f"academic_settings_block: {e}")

    # [6. કોલેજ જિયોફેન્સ સેટિંગ્સ (College Geofence Lat/Long & Timings)]:
    try:
        with db.engine.connect() as conn:
            existing = set()
            try:
                res = conn.execute(db.text("SHOW COLUMNS FROM college_settings")).fetchall()
                existing = {str(r[0]).lower() for r in res}
            except Exception:
                pass

            cs_cols = [
                ('college_type', "VARCHAR(50) NULL DEFAULT 'BCA'"),
                ('campus_latitude', 'DECIMAL(10,8) DEFAULT 24.15953750'),
                ('campus_longitude', 'DECIMAL(11,8) DEFAULT 72.40295313'),
                ('campus_radius_meters', 'INT DEFAULT 800'),
                ('campus_plus_code', "VARCHAR(100) DEFAULT '5C53+R58 Palanpur, Gujarat'"),
                ('college_start_time', "VARCHAR(10) DEFAULT '10:00'"),
                ('college_end_time', "VARCHAR(10) DEFAULT '17:00'")
            ]
            for col_name, col_def in cs_cols:
                if col_name.lower() not in existing:
                    try:
                        conn.execute(db.text(f"ALTER TABLE college_settings ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
                        existing.add(col_name.lower())
                        report["migrated"].append(f"college_settings.{col_name}")
                    except Exception as err:
                        report["errors"].append(f"college_settings.{col_name}: {err}")
    except Exception as e:
        report["errors"].append(f"college_settings_block: {e}")

    # [7. વિષયોની માર્ક્સ સ્કીમ (Subjects Internal & External Marks Scheme)]:
    try:
        with db.engine.connect() as conn:
            existing = set()
            try:
                res = conn.execute(db.text("SHOW COLUMNS FROM subjects")).fetchall()
                existing = {str(r[0]).lower() for r in res}
            except Exception:
                pass

            subj_cols = [
                ('internal_marks', 'INT NOT NULL DEFAULT 30'),
                ('external_marks', 'INT NOT NULL DEFAULT 70'),
                ('total_marks', 'INT NOT NULL DEFAULT 100'),
                ('component_config', 'TEXT NULL')
            ]
            for col_name, col_def in subj_cols:
                if col_name.lower() not in existing:
                    try:
                        conn.execute(db.text(f"ALTER TABLE subjects ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
                        existing.add(col_name.lower())
                        report["migrated"].append(f"subjects.{col_name}")
                    except Exception as err:
                        report["errors"].append(f"subjects.{col_name}: {err}")
    except Exception as e:
        report["errors"].append(f"subjects_block: {e}")

    # [8. ઇન્ટરનલ માર્ક્સ કોમ્પોનન્ટ્સ (Theory & Practical Evaluation Breakdown)]:
    try:
        with db.engine.connect() as conn:
            existing = set()
            try:
                res = conn.execute(db.text("SHOW COLUMNS FROM internal_marks")).fetchall()
                existing = {str(r[0]).lower() for r in res}
            except Exception:
                pass

            needed_cols = [
                ('test1', 'FLOAT NULL'), ('test2', 'FLOAT NULL'), ('test3', 'FLOAT NULL'),
                ('internal_exam', 'FLOAT NULL'),
                ('active_learning', 'FLOAT NULL'), ('class_assignment', 'FLOAT NULL'), ('home_assignment', 'FLOAT NULL'),
                ('attendance', 'FLOAT NULL'), ('practical_eval', 'FLOAT NULL'), ('viva', 'FLOAT NULL'),
                ('journal', 'FLOAT NULL'), ('component_data', 'TEXT NULL')
            ]
            for col_name, col_def in needed_cols:
                if col_name.lower() not in existing:
                    try:
                        conn.execute(db.text(f"ALTER TABLE internal_marks ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
                        existing.add(col_name.lower())
                        report["migrated"].append(f"internal_marks.{col_name}")
                    except Exception as err:
                        report["errors"].append(f"internal_marks.{col_name}: {err}")

            for q in ['quiz1', 'quiz2', 'quiz3', 'quiz4']:
                if q in existing:
                    try:
                        conn.execute(db.text(f"ALTER TABLE internal_marks DROP COLUMN {q}"))
                        conn.commit()
                    except Exception:
                        pass
    except Exception as e:
        report["errors"].append(f"internal_marks_block: {e}")

    # [9. પાસવર્ડ રીસેટ OTP અને બ્રુટ-ફોર્સ લોકઆઉટ (OTP Reset Columns)]:
    for tbl_name in ['students', 'faculty', 'admins']:
        try:
            with db.engine.connect() as conn:
                existing = set()
                try:
                    res = conn.execute(db.text(f"SHOW COLUMNS FROM {tbl_name}")).fetchall()
                    existing = {str(r[0]).lower() for r in res}
                except Exception:
                    continue

                otp_cols = [
                    ('reset_otp', 'VARCHAR(255) NULL'),
                    ('otp_expiry', 'DATETIME NULL'),
                    ('otp_attempts', 'INT NOT NULL DEFAULT 0'),
                    ('otp_blocked_until', 'DATETIME NULL')
                ]
                for col_name, col_def in otp_cols:
                    if col_name.lower() not in existing:
                        try:
                            conn.execute(db.text(f"ALTER TABLE {tbl_name} ADD COLUMN {col_name} {col_def}"))
                            conn.commit()
                            existing.add(col_name.lower())
                            report["migrated"].append(f"{tbl_name}.{col_name}")
                        except Exception as err:
                            report["errors"].append(f"{tbl_name}.{col_name}: {err}")
        except Exception as e:
            report["errors"].append(f"otp_{tbl_name}: {e}")

    # [10. કેમ્પસ ગેલેરી વ્યુ કાઉન્ટર (Gallery Items View Counter)]:
    try:
        with db.engine.connect() as conn:
            existing = set()
            try:
                res = conn.execute(db.text("SHOW COLUMNS FROM gallery_items")).fetchall()
                existing = {str(r[0]).lower() for r in res}
            except Exception:
                pass
            if 'views_count' not in existing:
                try:
                    conn.execute(db.text("ALTER TABLE gallery_items ADD COLUMN views_count INT NOT NULL DEFAULT 0"))
                    conn.commit()
                    report["migrated"].append("gallery_items.views_count")
                except Exception as err:
                    report["errors"].append(f"gallery_items: {err}")
    except Exception as e:
        report["errors"].append(f"gallery_items_block: {e}")

    # [11. લેક્ચર હાજરી સેશન્સ અને ક્યુઆર ટોકન (Lecture Attendance Sessions)]:
    try:
        with db.engine.connect() as conn:
            existing = set()
            try:
                res = conn.execute(db.text("SHOW COLUMNS FROM lecture_attendance_sessions")).fetchall()
                existing = {str(r[0]).lower() for r in res}
            except Exception:
                pass

            las_cols = [
                ('attendance_mode', "VARCHAR(20) DEFAULT 'Manual'"),
                ('qr_session_token', 'VARCHAR(255) NULL'),
                ('qr_session_expires_at', 'DATETIME NULL'),
                ('is_qr_active', 'TINYINT(1) DEFAULT 0'),
                ('session_type', "VARCHAR(20) DEFAULT 'Lecture'"),
                ('start_time', 'VARCHAR(10) NULL'),
                ('end_time', 'VARCHAR(10) NULL')
            ]
            for col_name, col_def in las_cols:
                if col_name.lower() not in existing:
                    try:
                        conn.execute(db.text(f"ALTER TABLE lecture_attendance_sessions ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
                        existing.add(col_name.lower())
                        report["migrated"].append(f"lecture_attendance_sessions.{col_name}")
                    except Exception as err:
                        report["errors"].append(f"lecture_attendance_sessions.{col_name}: {err}")
    except Exception as e:
        report["errors"].append(f"lecture_attendance_sessions_block: {e}")

    # [12. વિદ્યાર્થીઓની ક્યુઆર હાજરી અને જિયોફેન્સ વેરિફિકેશન (Lecture Attendance Students)]:
    try:
        with db.engine.connect() as conn:
            existing = set()
            try:
                res = conn.execute(db.text("SHOW COLUMNS FROM lecture_attendance_students")).fetchall()
                existing = {str(r[0]).lower() for r in res}
            except Exception:
                pass

            lat_cols = [
                ('marked_method', "VARCHAR(30) DEFAULT 'MANUAL'"),
                ('scanned_at', 'DATETIME NULL'),
                ('device_fingerprint', 'VARCHAR(500) NULL'),
                ('scan_latitude', 'DECIMAL(10,8) NULL'),
                ('scan_longitude', 'DECIMAL(11,8) NULL'),
                ('distance_meters', 'FLOAT NULL'),
                ('is_verified', 'TINYINT(1) DEFAULT 1')
            ]
            for col_name, col_def in lat_cols:
                if col_name.lower() not in existing:
                    try:
                        conn.execute(db.text(f"ALTER TABLE lecture_attendance_students ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
                        existing.add(col_name.lower())
                        report["migrated"].append(f"lecture_attendance_students.{col_name}")
                    except Exception as err:
                        report["errors"].append(f"lecture_attendance_students.{col_name}: {err}")
    except Exception as e:
        report["errors"].append(f"lecture_attendance_students_block: {e}")

    # [13. ડિફોલ્ટ ઓફિશિયલ કેમ્પસ પરિપત્રો (Official Public Notices Auto-Seed)]:
    try:
        from models import Notification
        pub_count = Notification.query.filter(
            Notification.target_audience.in_(['Guest', 'All']),
            Notification.is_active == True
        ).count()
        if pub_count < 2:
            seed_notices = [
                Notification(
                    title="Admissions Open for Academic Year 2026-27",
                    message="Applications are now open for BCA, B.Sc. IT, and Diploma programs for the academic year 2026-27. Prospective students can submit their online registration via the admissions portal.",
                    category="Academic",
                    target_audience="All",
                    target_semester=None,
                    target_division="All",
                    posted_by_role="Admin",
                    priority="Important",
                    is_active=True
                ),
                Notification(
                    title="Official Examination Guidelines & Schedule Published",
                    message="All semester students are hereby informed that the upcoming internal assessment and semester exam schedule has been released. Please check your respective course timetable.",
                    category="Exam",
                    target_audience="All",
                    target_semester=None,
                    target_division="All",
                    posted_by_role="Admin",
                    priority="Normal",
                    is_active=True
                ),
                Notification(
                    title="Smart Multimedia Classrooms & Campus Wi-Fi Operational",
                    message="CampusSync digital classrooms and campus-wide high-speed Wi-Fi infrastructure are now fully operational. Students and faculty may connect using their portal credentials.",
                    category="General",
                    target_audience="All",
                    target_semester=None,
                    target_division="All",
                    posted_by_role="Admin",
                    priority="Normal",
                    is_active=True
                )
            ]
            db.session.add_all(seed_notices)
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        report["errors"].append(f"default_notices_seed: {e}")

    # [14. વિદ્યાર્થીઓના ડિવાઇસ બાઇન્ડિંગ રીસેટ યુટિલિટી (Device Reset Utility)]:
    try:
        with db.engine.connect() as conn:
            conn.execute(db.text("UPDATE students SET device_fingerprint = NULL, device_model = NULL, device_bound_at = NULL, device_reset_allowed = 1 WHERE device_fingerprint IS NOT NULL"))
            conn.commit()
            report["migrated"].append("all_student_devices_reset")
    except Exception as e:
        report["errors"].append(f"reset_student_devices: {e}")

    # [15. સેમેસ્ટર 5 માર્ક્સ અને આર્કાઇવ્ડ એલ્યુમની રેકોર્ડ (Old Students Demonstration Archive)]:
    try:
        from models import Student, Subject, InternalMark, ResultDeclaration, Faculty, FacultySubjectAssignment, ArchivedStudent, ArchivedInternalMark
        import json

        target_enrollment = 'BCA15226000041'
        st = Student.query.filter(
            (Student.email == 'akashraval3709@gmail.com') |
            (Student.enrollment_no == target_enrollment)
        ).first()

        if st:
            target_sem = 5
            target_div = 'A'
            st.semester = target_sem
            st.division = target_div
            st.course = 'BCA'
            st.enrollment_no = target_enrollment
            st.status = 'Inactive'  # પાસ થઈ ગયેલા ઓલ્ડ સ્ટુડન્ટ્સ ટેબલમાં ખસેડવામાં આવેલ
            st.academic_year = '2024-25'
            st.mobile_number = '9328976261'

            fac = Faculty.query.filter_by(faculty_code='FAC002').first()
            subs = Subject.query.filter_by(semester=target_sem).all()
            if fac and subs:
                for s in subs:
                    assign = FacultySubjectAssignment.query.filter_by(subject_id=s.id, division=target_div).first()
                    if not assign:
                        assign = FacultySubjectAssignment(
                            faculty_id=fac.id,
                            subject_id=s.id,
                            division=target_div,
                            status='Active'
                        )
                        db.session.add(assign)
                    else:
                        assign.faculty_id = fac.id
                        assign.status = 'Active'

            marks_specs = {
                'BCA501': {'type': 'Theory', 'max': 50, 'test1': 14.0, 'test2': 13.0, 'test3': 12.0, 'best2': 13.5, 'internal_exam': 44.0, 'active_learning': 5.0, 'class_assignment': 5.0, 'home_assignment': 5.0, 'attendance': 4.5, 'total': 46.5},
                'BCA501A': {'type': 'Theory', 'max': 50, 'test1': 13.0, 'test2': 14.0, 'test3': 12.0, 'best2': 13.5, 'internal_exam': 45.0, 'active_learning': 5.0, 'class_assignment': 5.0, 'home_assignment': 5.0, 'attendance': 4.0, 'total': 45.15},
                'BCA501B': {'type': 'Practical', 'max': 25, 'internal_exam': 9.0, 'practical_eval': 9.5, 'viva': 4.0, 'journal': 4.5, 'total': 23.0},
                'BCA501C': {'type': 'Practical', 'max': 25, 'internal_exam': 9.0, 'practical_eval': 9.0, 'viva': 4.0, 'journal': 4.0, 'total': 22.0},
                'BCA502': {'type': 'Theory', 'max': 50, 'test1': 13.0, 'test2': 13.0, 'test3': 14.0, 'best2': 13.5, 'internal_exam': 43.0, 'active_learning': 5.0, 'class_assignment': 4.5, 'home_assignment': 5.0, 'attendance': 4.0, 'total': 45.0},
                'BCA502A': {'type': 'Theory', 'max': 50, 'test1': 14.0, 'test2': 12.0, 'test3': 13.0, 'best2': 13.5, 'internal_exam': 44.0, 'active_learning': 5.0, 'class_assignment': 5.0, 'home_assignment': 4.5, 'attendance': 4.5, 'total': 46.5},
                'BCA506': {'type': 'Practical', 'max': 25, 'internal_exam': 9.5, 'practical_eval': 9.5, 'viva': 4.5, 'journal': 4.5, 'total': 24.0}
            }

            for ay in ['2024-25', '2026-27']:
                for s in subs:
                    spec = marks_specs.get(s.subject_code)
                    if not spec:
                        continue
                    m = InternalMark.query.filter_by(
                        student_id=st.id,
                        subject_id=s.id,
                        semester=target_sem,
                        academic_year=ay
                    ).first()
                    if not m:
                        m = InternalMark(
                            student_id=st.id,
                            subject_id=s.id,
                            semester=target_sem,
                            academic_year=ay
                        )
                        db.session.add(m)
                    m.marks_obtained = spec['total']
                    m.max_marks = spec['max']
                    if spec['type'] == 'Theory':
                        m.test1, m.test2, m.test3 = spec['test1'], spec['test2'], spec['test3']
                        m.internal_exam = spec['internal_exam']
                        m.active_learning, m.class_assignment, m.home_assignment = spec['active_learning'], spec['class_assignment'], spec['home_assignment']
                        m.attendance = spec['attendance']
                        m.practical_eval, m.viva, m.journal = None, None, None
                        cdata = {
                            "type": "Theory", "save_type": "final", "status": "final", "is_final": True,
                            "test1": spec['test1'], "test2": spec['test2'], "test3": spec['test3'], "best2": spec['best2'],
                            "internal_exam": spec['internal_exam'], "active_learning": spec['active_learning'],
                            "class_assignment": spec['class_assignment'], "home_assignment": spec['home_assignment'],
                            "attendance": spec['attendance'], "total": spec['total'], "max_marks": spec['max']
                        }
                    else:
                        m.test1, m.test2, m.test3 = None, None, None
                        m.active_learning, m.class_assignment, m.home_assignment, m.attendance = None, None, None, None
                        m.internal_exam = spec['internal_exam']
                        m.practical_eval, m.viva, m.journal = spec['practical_eval'], spec['viva'], spec['journal']
                        cdata = {
                            "type": "Practical", "save_type": "final", "status": "final", "is_final": True,
                            "internal_exam": spec['internal_exam'], "practical_eval": spec['practical_eval'],
                            "viva": spec['viva'], "journal": spec['journal'], "total": spec['total'], "max_marks": spec['max']
                        }
                    m.component_data = json.dumps(cdata)

                for target_d in ['All', 'A']:
                    decl = ResultDeclaration.query.filter_by(academic_year=ay, semester=target_sem, division=target_d).first()
                    if not decl:
                        decl = ResultDeclaration(academic_year=ay, semester=target_sem, division=target_d, is_declared=True, declared_at=datetime.utcnow(), declared_by=1)
                        db.session.add(decl)
                    else:
                        decl.is_declared = True

            # ArchivedStudent રેકોર્ડ અપડેટ
            arch_st = ArchivedStudent.query.filter(
                (ArchivedStudent.enrollment_no == target_enrollment) |
                (ArchivedStudent.email == st.email)
            ).first()

            if not arch_st:
                arch_st = ArchivedStudent(
                    original_student_id=st.id,
                    roll_number=st.roll_number or '1',
                    enrollment_no=target_enrollment,
                    full_name=st.full_name,
                    email=st.email,
                    mobile=st.mobile,
                    dob=st.dob,
                    course=st.course or 'BCA',
                    final_semester=target_sem,
                    division=target_div,
                    academic_year='2024-25',
                    profile_photo=st.profile_photo or 'default-avatar.png',
                    status='Archived',
                    archived_at=datetime.utcnow()
                )
                db.session.add(arch_st)
            else:
                arch_st.original_student_id = st.id
                arch_st.enrollment_no = target_enrollment
                arch_st.full_name = st.full_name
                arch_st.email = st.email
                arch_st.final_semester = target_sem
                arch_st.academic_year = '2024-25'
                arch_st.status = 'Archived'

            # ArchivedInternalMark ટેબલમાં માર્ક્સ ટ્રાન્સફર
            for s in subs:
                spec = marks_specs.get(s.subject_code)
                if not spec:
                    continue
                arch_m = ArchivedInternalMark.query.filter_by(
                    enrollment_no=target_enrollment,
                    subject_id=s.id,
                    semester=target_sem,
                    academic_year='2024-25'
                ).first()
                if not arch_m:
                    arch_m = ArchivedInternalMark(
                        enrollment_no=target_enrollment,
                        student_name=st.full_name,
                        subject_id=s.id,
                        subject_code=s.subject_code,
                        subject_name=s.subject_name,
                        semester=target_sem,
                        academic_year='2024-25',
                        marks_obtained=spec['total'],
                        max_marks=spec['max'],
                        component_data=json.dumps(spec),
                        archived_at=datetime.utcnow()
                    )
                    db.session.add(arch_m)
                else:
                    arch_m.marks_obtained = spec['total']
                    arch_m.max_marks = spec['max']
                    arch_m.component_data = json.dumps(spec)

            db.session.commit()
            report["migrated"].append("akash_raval_sem5_archived_alumni")
    except Exception as e:
        db.session.rollback()
        report["errors"].append(f"akash_raval_sem5_archive: {e}")

    return report


# ------------------------------------------------------------------------------
# 8. STARTUP SCHEMA MIGRATION TRIGGER
# ------------------------------------------------------------------------------
# એપ્લિકેશન શરૂ થતી વખતે જ તમામ માઇગ્રેશન એકવાર ચલાવી લે છે
with app.app_context():
    try:
        migration_results = run_all_database_migrations()
        print(f"DATABASE MIGRATION SUMMARY: Migrated={len(migration_results['migrated'])}, Errors={len(migration_results['errors'])}")
    except Exception as e:
        print(f"Database migration top-level error: {e}")
        traceback.print_exc()

# ગમે ત્યારે મેન્યુઅલી બ્રાઉઝરમાંથી માઇગ્રેશન ટ્રિગર કરવા માટેનું ડેડિકેટેડ એન્ડપોઇન્ટ
@app.route('/system/migrate-db', methods=['GET', 'POST'])
def trigger_database_migration():
    """Manual or automated endpoint to run database migrations on Railway."""
    res = run_all_database_migrations()
    return {
        "status": "success",
        "migrated_count": len(res["migrated"]),
        "migrated_items": res["migrated"],
        "error_count": len(res["errors"]),
        "errors": res["errors"]
    }

# ------------------------------------------------------------------------------
# 9. DATABASE CONNECTION HEALTH CHECK ON STARTUP
# ------------------------------------------------------------------------------
print("--------------------------------------------------")
print("Initializing CampusSync Application Backend...")
if test_connection():
    print("SUCCESS: Database connection to 'campussync' established successfully!")
else:
    print("WARNING: Could not connect to MySQL database. Please check your .env settings or import database/campussync.sql.")
print("--------------------------------------------------")


# ------------------------------------------------------------------------------
# 10. STATIC ASSETS & UNIVERSAL FILE SERVING HANDLERS
# ------------------------------------------------------------------------------
@app.route('/assets/<path:filename>')
def serve_assets(filename):
    """Fallback handler for static assets (/assets/...)"""
    static_dir = os.path.join(app.root_path, 'static')
    return send_from_directory(static_dir, filename)

@app.route('/uploads/<path:filename>', endpoint='global_serve_uploads')
def global_serve_uploads(filename):
    """
    યુનિવર્સલ ફાઇલ સર્વિંગ હેન્ડલર:
    વિદ્યાર્થીઓના પ્રોફાઇલ ફોટા, ફેકલ્ટી ફોટા, નોટિસ દસ્તાવેજો (PDF/Word/Images)
    વગેરેને બ્રાઉઝર પર સુરક્ષિત રીતે ડિલિવર કરે છે.
    """
    uploads_dir = os.path.join(app.root_path, 'uploads')
    target_path = os.path.join(uploads_dir, filename)
    if not os.path.exists(target_path):
        if filename.startswith('student/'):
            alt = os.path.join(uploads_dir, 'students', filename[len('student/'):])
            if os.path.exists(alt):
                return send_from_directory(os.path.join(uploads_dir, 'students'), filename[len('student/'):])
        elif filename.startswith('students/'):
            alt = os.path.join(uploads_dir, 'student', filename[len('students/'):])
            if os.path.exists(alt):
                return send_from_directory(os.path.join(uploads_dir, 'student'), filename[len('students/'):])
    return send_from_directory(uploads_dir, filename)


# ------------------------------------------------------------------------------
# 11. BREVO EMAIL API PRODUCTION TEST ROUTE
# ------------------------------------------------------------------------------
@app.route('/test-email')
@app.route('/test-mail')
def test_email_route():
    """
    Brevo HTTPS API દ્વારા ઈમેલ ડિલિવરી ચકાસવા માટેનું ટેસ્ટ એન્ડપોઇન્ટ.
    બ્રાઉઝરમાં /test-email ખોલીને લાઈવ ચેક કરી શકાય છે.
    """
    to_email = request.args.get('to', app.config.get('MAIL_DEFAULT_SENDER', 'devidparmar8954@gmail.com'))
    from mail.brevo_service import is_brevo_configured, send_brevo_email
    brevo_active = is_brevo_configured()
    
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 20px auto; padding: 24px; border: 1px solid #e2e8f0; border-radius: 12px; background: #ffffff;">
        <h2 style="color: #2563eb; margin-top: 0;">🎉 CampusSync - Brevo Email Test Successful!</h2>
        <p style="font-size: 15px; color: #334155;">This is a live test email sent directly from <strong>CampusSync ERP on Railway</strong>.</p>
        <div style="background: #f8fafc; padding: 14px; border-radius: 8px; border: 1px solid #e2e8f0; margin: 16px 0;">
            <p style="margin: 4px 0;"><strong>Brevo API Status:</strong> <span style="color: #16a34a; font-weight: bold;">ACTIVE (Port 443 HTTPS)</span></p>
            <p style="margin: 4px 0;"><strong>Timestamp:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
            <p style="margin: 4px 0;"><strong>Delivered To:</strong> {to_email}</p>
        </div>
        <p style="color: #64748b; font-size: 13px; margin-bottom: 0;">If you see this in your inbox, your email delivery system is working 100% on Railway!</p>
    </div>
    """
    
    if brevo_active:
        success, info = send_brevo_email(
            to_email=to_email,
            subject="CampusSync - Brevo Email Test Successful",
            html_content=html,
            to_name="CampusSync Admin",
            text_content="CampusSync live email test via Brevo was successful!"
        )
        if success:
            return f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 50px auto; padding: 30px; border-radius: 14px; background: #ecfdf5; border: 1px solid #a7f3d0; color: #065f46; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">
                <h2 style="margin-top: 0; color: #047857;">✅ Email Sent Successfully via Brevo!</h2>
                <p style="font-size: 15px;">A live test email was successfully dispatched to: <strong>{to_email}</strong></p>
                <p style="font-size: 13px; color: #065f46; background: #d1fae5; padding: 10px 14px; border-radius: 8px;"><strong>Brevo Message ID:</strong> <code>{info}</code></p>
                <p style="margin-top: 20px; font-weight: 500;">Your Railway environment, Brevo API key, and HTTPS email delivery are functioning <strong>100% correctly</strong>!</p>
                <div style="margin-top: 24px;">
                    <a href="/admin/email-logs" style="display: inline-block; background: #059669; color: #ffffff; padding: 10px 18px; border-radius: 8px; text-decoration: none; font-weight: 600; margin-right: 10px;">Go to Email Logs</a>
                    <a href="/admin/login" style="display: inline-block; background: #ffffff; color: #059669; border: 1px solid #059669; padding: 10px 18px; border-radius: 8px; text-decoration: none; font-weight: 600;">Admin Login</a>
                </div>
            </div>
            """
        else:
            return f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 50px auto; padding: 30px; border-radius: 14px; background: #fef2f2; border: 1px solid #fecaca; color: #991b1b; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">
                <h2 style="margin-top: 0; color: #b91c1c;">❌ Brevo Email Delivery Failed</h2>
                <p>Attempted to send to: <strong>{to_email}</strong></p>
                <div style="background: #fee2e2; padding: 12px 14px; border-radius: 8px; font-family: monospace; font-size: 13px; margin: 16px 0; word-break: break-all;">
                    {info}
                </div>
                <p style="font-size: 14px; color: #7f1d1d;"><strong>Troubleshooting Tip:</strong> Ensure that your <code>MAIL_DEFAULT_SENDER</code> in Railway matches the verified sender in your Brevo account.</p>
            </div>
            """, 500
    else:
        return f"""
        <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 50px auto; padding: 30px; border-radius: 14px; background: #fffbeb; border: 1px solid #fde68a; color: #92400e; box-shadow: 0 4px 12px rgba(0,0,0,0.05);">
            <h2 style="margin-top: 0; color: #b45309;">⚠️ BREVO_API_KEY Not Found</h2>
            <p>The <code>BREVO_API_KEY</code> environment variable is missing or empty on this server.</p>
            <p>Please ensure you added <code>BREVO_API_KEY</code> in Railway Dashboard ➔ Variables and clicked <strong>Deploy</strong>.</p>
        </div>
        """, 400


# ------------------------------------------------------------------------------
# 12. HTTP PRODUCTION ERROR HANDLERS
# ------------------------------------------------------------------------------
from flask import render_template

@app.errorhandler(404)
def handle_404_error(e):
    """પેજ ન મળે ત્યારે યુઝર-ફ્રેન્ડલી 404 કસ્ટમ પેજ દર્શાવે છે."""
    return render_template('public/404.html'), 404

@app.errorhandler(500)
def handle_500_error(e):
    """સર્વર એરર આવે ત્યારે રોલબેક કરીને સુંદર 500 પેજ દર્શાવે છે."""
    try:
        db.session.rollback()
    except Exception:
        pass
    from flask import request
    app.logger.error(f"500 Internal Server Error at {request.path}: {e}", exc_info=True)
    return render_template('public/500.html'), 500

@app.errorhandler(403)
def handle_403_error(e):
    """પરમિશન ન હોય તેવા અનઓથોરાઇઝ્ડ એક્સેસ માટે 403 Forbidden પેજ."""
    return render_template('public/403.html'), 403


# ------------------------------------------------------------------------------
# 13. SERVER RUNNER
# ------------------------------------------------------------------------------
if __name__ == '__main__':
    # પોર્ટ અને હોસ્ટ એન્વાયર્નમેન્ટ વેરીએબલ્સમાંથી (Railway/Local) મેળવે છે
    port = int(os.environ.get('PORT', 5000))
    host = os.environ.get('HOST', '0.0.0.0')
    debug = os.environ.get('FLASK_DEBUG', 'False').lower() in ('true', '1', 't')
    app.run(host=host, port=port, debug=debug)
