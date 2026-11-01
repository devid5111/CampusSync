"""
CampusSync ERP - Notification Service
====================================
File: services/notification_service.py
Handles role-based notifications, category filtering, assignment timelines,
attachment uploads, urgency alerts, and live countdown data.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone
from werkzeug.utils import secure_filename
from flask import current_app
from extensions import db
from models import Notification, NotificationRead, Subject, FacultySubjectAssignment, Student, Admin, Faculty


IST_ZONE = timezone(timedelta(hours=5, minutes=30))


def get_ist_now():
    """Returns current naive datetime in Indian Standard Time (IST, UTC+5:30)."""
    return datetime.now(IST_ZONE).replace(tzinfo=None)


def auto_clean_expired_broadcast_notices():
    """
    Auto-deletes non-assignment broadcast notices whose timeline expiry (end_date) has arrived in IST.
    Protects academic assignments (category 'Assignment Submit Date') and student submissions from deletion.
    """
    now = get_ist_now()
    try:
        from models import AssignmentSubmission
        expired_notices = Notification.query.filter(
            Notification.end_date.isnot(None),
            Notification.end_date <= now,
            Notification.category != 'Assignment Submit Date'
        ).all()

        deleted_any = False
        for n in expired_notices:
            has_sub = AssignmentSubmission.query.filter_by(notification_id=n.id).first()
            if not has_sub:
                if n.photo_file:
                    try:
                        fpath = os.path.join(current_app.root_path, 'uploads', 'notifications', n.photo_file)
                        if os.path.exists(fpath):
                            os.remove(fpath)
                    except Exception:
                        pass
                db.session.delete(n)
                deleted_any = True

        if deleted_any:
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.warning(f"[auto_clean_expired_broadcast_notices Error] {e}")


ALLOWED_EXTENSIONS = {
    'png', 'jpg', 'jpeg', 'webp', 'gif',
    'pdf', 'doc', 'docx', 'txt', 'zip'
}

IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
DOC_EXTENSIONS = {'pdf', 'doc', 'docx', 'txt', 'zip'}


def allowed_file(filename):
    """Checks if the uploaded file has a permitted extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_file_type(filename):
    """Determines whether file is an image or document."""
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else ''
    if ext in IMAGE_EXTENSIONS:
        return 'image'
    elif ext in DOC_EXTENSIONS:
        return 'document'
    return 'other'


def save_notification_file(file_storage):
    """
    Saves uploaded file to 'uploads/notifications/' with a secure unique filename.
    Returns (saved_filename, file_type) or (None, None) if invalid or empty.
    """
    if not file_storage or file_storage.filename == '':
        return None, None

    if not allowed_file(file_storage.filename):
        return None, None

    upload_dir = os.path.join(current_app.root_path, 'uploads', 'notifications')
    os.makedirs(upload_dir, exist_ok=True)

    original_name = secure_filename(file_storage.filename)
    unique_prefix = uuid.uuid4().hex[:10]
    final_filename = f"{unique_prefix}_{original_name}"
    save_path = os.path.join(upload_dir, final_filename)

    file_storage.save(save_path)
    file_type = get_file_type(final_filename)

    return final_filename, file_type


def parse_datetime(dt_str):
    """Parses datetime from standard HTML datetime-local input (YYYY-MM-DDTHH:MM) or Date (YYYY-MM-DD)."""
    if not dt_str:
        return None
    dt_str = dt_str.strip()
    formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d']
    for fmt in formats:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    return None


def ensure_notification_columns():
    """
    Bulletproof self-healing schema verification for the 'notifications' table.
    Queries MySQL directly to check if all required columns exist, adding any missing ones.
    """
    needed_cols = [
        ('category', "VARCHAR(50) NOT NULL DEFAULT 'General'"),
        ('photo_file', "VARCHAR(255) NULL"),
        ('file_type', "VARCHAR(20) NULL"),
        ('start_date', "DATETIME NULL"),
        ('end_date', "DATETIME NULL"),
        ('posted_by_role', "ENUM('Admin','Faculty') NOT NULL DEFAULT 'Admin'"),
        ('admin_id', "INT NULL"),
        ('faculty_id', "INT NULL"),
        ('target_audience', "ENUM('All','Guest','Faculty','Student') NOT NULL DEFAULT 'All'"),
        ('target_semester', "SMALLINT NULL"),
        ('target_division', "VARCHAR(10) NULL DEFAULT 'All'"),
        ('subject_id', "INT NULL"),
        ('target_student_id', "INT NULL"),
        ('priority', "ENUM('Normal','Important','Urgent') NOT NULL DEFAULT 'Normal'"),
        ('is_active', "TINYINT(1) NOT NULL DEFAULT 1"),
        ('is_final_saved', "TINYINT(1) NOT NULL DEFAULT 0"),
        ('final_saved_at', "DATETIME NULL"),
        ('final_saved_by', "INT NULL")
    ]
    try:
        from extensions import db
        with db.engine.connect() as conn:
            existing = set()
            tbl_target = 'notifications'
            try:
                res = conn.execute(db.text("SHOW COLUMNS FROM notifications")).fetchall()
                existing = {str(r[0]).lower() for r in res}
            except Exception:
                try:
                    res = conn.execute(db.text("SHOW COLUMNS FROM Notifications")).fetchall()
                    existing = {str(r[0]).lower() for r in res}
                    tbl_target = 'Notifications'
                except Exception:
                    return False

            for col_name, col_def in needed_cols:
                if col_name.lower() not in existing:
                    try:
                        conn.execute(db.text(f"ALTER TABLE {tbl_target} ADD COLUMN {col_name} {col_def}"))
                        conn.commit()
                        existing.add(col_name.lower())
                        if current_app:
                            current_app.logger.info(f"Auto-migrated missing column '{col_name}' to '{tbl_target}'.")
                    except Exception:
                        pass

            try:
                conn.execute(db.text(f"ALTER TABLE {tbl_target} CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
                conn.commit()
            except Exception:
                pass
            return True
    except Exception as e:
        if current_app:
            current_app.logger.warning(f"ensure_notification_columns error: {e}")
        return False


def ensure_notification_reads_table():
    """Guarantees notification_reads table exists for unread tracking."""
    try:
        from extensions import db
        with db.engine.connect() as conn:
            conn.execute(db.text("""
                CREATE TABLE IF NOT EXISTS notification_reads (
                    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                    notification_id INT NOT NULL,
                    user_role ENUM('Admin', 'Faculty', 'Student') NOT NULL,
                    user_id INT NOT NULL,
                    read_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uq_notification_user_read (notification_id, user_role, user_id),
                    KEY idx_user_role_id (user_role, user_id)
                )
            """))
            conn.commit()
    except Exception:
        pass


def create_notification(title, message, category, posted_by_role,
                        admin_id=None, faculty_id=None, target_audience='All',
                        target_semester=None, target_division='All', subject_id=None,
                        start_date=None, end_date=None, priority='Normal',
                        photo_file=None, file_type=None, target_student_id=None):
    """Creates a new Notification entry in database with foreign-key resilience and transaction safety."""
    # Convert dates if given as strings
    if isinstance(start_date, str):
        start_date = parse_datetime(start_date)
    if isinstance(end_date, str):
        end_date = parse_datetime(end_date)

    if target_semester in ('', 'All', None):
        target_semester = None
    else:
        try:
            target_semester = int(target_semester)
        except (ValueError, TypeError):
            target_semester = None

    if target_division in ('', 'None', None):
        target_division = 'All'

    # Validate foreign keys before inserting to prevent IntegrityError crashes
    if subject_id in ('', 'None', None):
        subject_id = None
    else:
        try:
            subject_id = int(subject_id)
            if not Subject.query.get(subject_id):
                subject_id = None
        except (ValueError, TypeError, Exception):
            subject_id = None

    if target_student_id in ('', 'None', None):
        target_student_id = None
    else:
        try:
            target_student_id = int(target_student_id)
            if not Student.query.get(target_student_id):
                target_student_id = None
        except (ValueError, TypeError, Exception):
            target_student_id = None

    if admin_id in ('', 'None', None):
        admin_id = None
    else:
        try:
            admin_id = int(admin_id)
            if not Admin.query.get(admin_id):
                admin_id = None
        except (ValueError, TypeError, Exception):
            admin_id = None

    if faculty_id in ('', 'None', None):
        faculty_id = None
    else:
        try:
            faculty_id = int(faculty_id)
            if not Faculty.query.get(faculty_id):
                faculty_id = None
        except (ValueError, TypeError, Exception):
            faculty_id = None

    try:
        notification = Notification(
            title=(title or '').strip(),
            message=(message or '').strip(),
            category=(category or 'General').strip(),
            photo_file=photo_file,
            file_type=file_type,
            start_date=start_date,
            end_date=end_date,
            posted_by_role=posted_by_role if posted_by_role in ('Admin', 'Faculty') else 'Admin',
            admin_id=admin_id,
            faculty_id=faculty_id,
            target_audience=target_audience if target_audience in ('All', 'Guest', 'Faculty', 'Student') else 'All',
            target_semester=target_semester,
            target_division=target_division,
            subject_id=subject_id,
            target_student_id=target_student_id,
            priority=priority if priority in ('Normal', 'Important', 'Urgent') else 'Normal',
            is_active=True
        )

        db.session.add(notification)
        db.session.commit()
        return notification
    except Exception as e:
        db.session.rollback()
        err_str = str(e)
        if '1054' in err_str or 'Unknown column' in err_str or 'target_student_id' in err_str:
            ensure_notification_columns()
            try:
                db.session.add(notification)
                db.session.commit()
                return notification
            except Exception as retry_err:
                db.session.rollback()
                current_app.logger.error(f"[create_notification Retry Error] {retry_err}", exc_info=True)
                return None
        current_app.logger.error(f"[create_notification Error] {e}", exc_info=True)
        return None


def get_public_notices(limit=10):
    """Fetches active notifications targeted to Guest or All for the college homepage."""
    auto_clean_expired_broadcast_notices()
    def _fetch():
        return Notification.query.filter(
            Notification.target_audience.in_(['Guest', 'All']),
            Notification.is_active == True
        ).order_by(Notification.created_at.desc()).limit(limit).all()

    try:
        return _fetch()
    except Exception as e:
        db.session.rollback()
        err_str = str(e)
        if '1054' in err_str or 'Unknown column' in err_str or 'target_student_id' in err_str:
            ensure_notification_columns()
            try:
                return _fetch()
            except Exception:
                db.session.rollback()
        current_app.logger.warning(f"[get_public_notices Error] {e}")
        return []


def get_faculty_notices(filter_by_cycle=True):
    """Fetches active campus notices intended for Faculty or All, filtered by active semester cycle."""
    auto_clean_expired_broadcast_notices()
    def _fetch():
        return Notification.query.filter(
            Notification.target_audience.in_(['Faculty', 'All']),
            Notification.is_active == True
        ).order_by(Notification.created_at.desc()).all()

    try:
        notices = _fetch()
    except Exception as e:
        db.session.rollback()
        err_str = str(e)
        if '1054' in err_str or 'Unknown column' in err_str or 'target_student_id' in err_str:
            ensure_notification_columns()
            try:
                notices = _fetch()
            except Exception:
                db.session.rollback()
                current_app.logger.warning(f"[get_faculty_notices Retry Error] {e}")
                return []
        else:
            current_app.logger.warning(f"[get_faculty_notices Error] {e}")
            return []

    if not filter_by_cycle:
        return notices

    try:
        from services.academic_service import get_active_semesters
        active_sems = get_active_semesters()
    except Exception:
        active_sems = [1, 3, 5]

    filtered = []
    for n in notices:
        try:
            if n.target_semester is not None:
                if n.target_semester in active_sems:
                    filtered.append(n)
            elif n.subject and n.subject.semester is not None:
                if n.subject.semester in active_sems:
                    filtered.append(n)
            else:
                filtered.append(n)
        except Exception:
            filtered.append(n)
    return filtered


def get_faculty_created_notices(faculty_id, filter_by_cycle=True):
    """Fetches notices posted by this specific faculty member, filtered by active semester cycle."""
    def _fetch():
        return Notification.query.filter_by(
            faculty_id=faculty_id,
            posted_by_role='Faculty'
        ).order_by(Notification.created_at.desc()).all()

    try:
        notices = _fetch()
    except Exception as e:
        db.session.rollback()
        err_str = str(e)
        if '1054' in err_str or 'Unknown column' in err_str or 'target_student_id' in err_str:
            ensure_notification_columns()
            try:
                notices = _fetch()
            except Exception:
                db.session.rollback()
                current_app.logger.warning(f"[get_faculty_created_notices Retry Error] {e}")
                return []
        else:
            current_app.logger.warning(f"[get_faculty_created_notices Error] {e}")
            return []

    if not filter_by_cycle:
        return notices

    try:
        from services.academic_service import get_active_semesters
        active_sems = get_active_semesters()
    except Exception:
        active_sems = [1, 3, 5]

    filtered = []
    for n in notices:
        try:
            if n.target_semester is not None:
                if n.target_semester in active_sems:
                    filtered.append(n)
            elif n.subject and n.subject.semester is not None:
                if n.subject.semester in active_sems:
                    filtered.append(n)
            else:
                filtered.append(n)
        except Exception:
            filtered.append(n)
    return filtered


def get_admin_feed_notices(filter_by_cycle=True):
    """
    auto_clean_expired_broadcast_notices()
    Fetches active notifications meant specifically for Administrator personal feed & topbar bell.
    Strictly includes only notices with target_audience in ('Admin', 'All').
    STRICTLY EXCLUDES student-specific notices (assignments, device reminders, etc.) and faculty-only notices.
    """
    def _fetch():
        return Notification.query.filter(
            Notification.target_audience.in_(['Admin', 'All']),
            Notification.is_active == True
        ).order_by(Notification.created_at.desc()).all()

    try:
        notices = _fetch()
    except Exception as e:
        db.session.rollback()
        err_str = str(e)
        if '1054' in err_str or 'Unknown column' in err_str or 'target_student_id' in err_str:
            ensure_notification_columns()
            try:
                notices = _fetch()
            except Exception:
                db.session.rollback()
                current_app.logger.warning(f"[get_admin_feed_notices Retry Error] {e}")
                return []
        else:
            current_app.logger.warning(f"[get_admin_feed_notices Error] {e}")
            return []

    if not filter_by_cycle:
        return notices

    try:
        from services.academic_service import get_active_semesters
        active_sems = get_active_semesters()
    except Exception:
        active_sems = [1, 3, 5]

    filtered = []
    for n in notices:
        try:
            if n.target_semester is not None:
                if n.target_semester in active_sems:
                    filtered.append(n)
            elif n.subject and n.subject.semester is not None:
                if n.subject.semester in active_sems:
                    filtered.append(n)
            else:
                filtered.append(n)
        except Exception:
            filtered.append(n)
    return filtered


def get_all_admin_notices(filter_by_cycle=True):
    """Fetches all notices (for Admin management table), filtered by active semester cycle."""
    auto_clean_expired_broadcast_notices()
    def _fetch():
        return Notification.query.order_by(Notification.created_at.desc()).all()

    try:
        notices = _fetch()
    except Exception as e:
        db.session.rollback()
        err_str = str(e)
        if '1054' in err_str or 'Unknown column' in err_str or 'target_student_id' in err_str:
            ensure_notification_columns()
            try:
                notices = _fetch()
            except Exception:
                db.session.rollback()
                current_app.logger.warning(f"[get_all_admin_notices Retry Error] {e}")
                return []
        else:
            current_app.logger.warning(f"[get_all_admin_notices Error] {e}")
            return []

    if not filter_by_cycle:
        return notices

    try:
        from services.academic_service import get_active_semesters
        active_sems = get_active_semesters()
    except Exception:
        active_sems = [1, 3, 5]

    filtered = []
    for n in notices:
        try:
            if n.target_semester is not None:
                if n.target_semester in active_sems:
                    filtered.append(n)
            elif n.subject and n.subject.semester is not None:
                if n.subject.semester in active_sems:
                    filtered.append(n)
            else:
                filtered.append(n)
        except Exception:
            filtered.append(n)
    return filtered


def get_student_notices(student, category=None):
    """
    Fetches all active notices visible to a specific student:
    1. Direct Individual Notice: If target_student_id matches this student, ALWAYS included.
    2. Student or All audience: Matches student's enrolled semester (or All) and division (or All).
    3. Resilient category filtering (All, Assignments, Tests & Exams, Subject Related, General).
    4. Bulk device binding reminder only cleared if student actually has an active registered passkey.
    5. Never drops notices for the student's enrolled semester.
    """
    if not student:
        return []

    auto_clean_expired_broadcast_notices()
    now = get_ist_now()

    sem = student.semester
    div = str(student.division or 'All').strip().upper()
    fp = student.device_fingerprint or ''
    is_really_bound = bool(
        fp and 
        not fp.startswith('HW-') and 
        not fp.startswith('DEV-') and 
        not fp.startswith('PIN-') and 
        not getattr(student, 'device_reset_allowed', 0)
    )

    try:
        # Base query: Active notices targeted to Student or All
        query = Notification.query.filter(
            Notification.is_active == True,
            db.or_(
                Notification.target_audience == 'All',
                Notification.target_audience == 'Student'
            )
        )

        cat_clean = (category or 'All').strip()
        if cat_clean and cat_clean.lower() != 'all':
            if cat_clean.lower() in ('assignment submit date', 'assignments', 'assignment'):
                query = query.filter(Notification.category.in_(['Assignment Submit Date', 'Assignment']))
            elif cat_clean.lower() in ('test', 'tests & exams', 'exam', 'exams'):
                query = query.filter(Notification.category.in_(['Test', 'Exam', 'Tests & Exams']))
            elif cat_clean.lower() in ('subject related', 'subject'):
                query = query.filter(Notification.category == 'Subject Related')
            elif cat_clean.lower() in ('general', 'administrative', 'circulars', 'circular'):
                query = query.filter(Notification.category.in_([
                    'General', 'Academic', 'Events', 'Administrative', 'Holiday', 'Device Binding', 'Other'
                ]))
            else:
                query = query.filter(Notification.category == cat_clean)

        notices = query.order_by(Notification.created_at.desc()).all()
    except Exception as e:
        db.session.rollback()
        err_str = str(e)
        if '1054' in err_str or 'Unknown column' in err_str or 'target_student_id' in err_str:
            ensure_notification_columns()
            try:
                notices = query.order_by(Notification.created_at.desc()).all()
            except Exception:
                db.session.rollback()
                current_app.logger.warning(f"[get_student_notices Retry Error] {e}")
                return []
        else:
            current_app.logger.warning(f"[get_student_notices Error] {e}")
            return []

    filtered = []
    seen_device_notice = False
    for n in notices:
        try:
            is_device_notice = (
                n.category == 'Device Binding' or 
                'Device Registration' in (n.title or '') or 
                'ડિવાઇસ' in (n.title or '') or 
                'Passkey' in (n.title or '')
            )
            # If student's mobile is already bound, NEVER show any device registration reminder!
            if is_device_notice and is_really_bound:
                continue

            # 1. Direct Individual Notice: Targeted directly to this student
            if getattr(n, 'target_student_id', None) is not None:
                if n.target_student_id == student.id:
                    if is_device_notice:
                        if seen_device_notice:
                            continue
                        seen_device_notice = True
                    n.deadline_info = calculate_deadline_info(n)
                    filtered.append(n)
                continue

            # Safety: If a broadcast notice contains another student's name, DO NOT leak it!
            if is_device_notice and 'Dear ' in (n.message or '') and (student.full_name or '') not in (n.message or ''):
                continue

            # 2. Audience filter: If targeted to Student or All, match enrolled Semester & Division
            if n.target_audience in ('Student', 'All'):
                if n.target_semester is not None and n.target_semester != sem:
                    continue
                n_div = str(n.target_division or 'All').strip().upper()
                if n_div not in ('ALL', '', div):
                    continue

            # 3. Subject filter if specified
            if n.subject_id is not None:
                if n.subject and n.subject.semester is not None and n.subject.semester != sem:
                    continue

            # Schedule / Expiry filtering for broadcast notices:
            if n.start_date and n.start_date > now:
                continue
            if n.category != 'Assignment Submit Date' and n.end_date and n.end_date <= now:
                continue

            # 4. Device Binding Bulk Alert Filter (only 1 device reminder banner on dashboard):
            if is_device_notice:
                if seen_device_notice:
                    continue
                seen_device_notice = True

            n.deadline_info = calculate_deadline_info(n)
            filtered.append(n)
        except Exception:
            try:
                n.deadline_info = calculate_deadline_info(n)
            except Exception:
                pass
            filtered.append(n)

    return filtered


def calculate_deadline_info(notice):
    """
    Calculates remaining time, urgency, countdown flags, and extra-day banner state for a notice.
    All calculations are strictly based on Indian Standard Time (IST, UTC+5:30).
    - Active countdown: starts when <= 2 days (48 hours / 172800 seconds) remain until end_date.
    - Extra 1 Day Grace Banner: after deadline passes, remains visible in student urgent banner
      for an extra 1 day (up to 24 hours / 86400 seconds past end_date).
      During this extra day, the timer is stopped and shows 'Assignment Date Over • Submission Closed'.
    - After Extra 1 Day (> 24 hours past end_date): completely excluded from urgent banner.
    """
    if not notice.end_date:
        return {
            'has_deadline': False,
            'is_expired': False,
            'is_extra_day': False,
            'is_completely_expired': False,
            'is_urgent': (getattr(notice, 'priority', 'Normal') == 'Urgent'),
            'total_seconds': 0,
            'days': 0,
            'hours': 0,
            'minutes': 0,
            'seconds': 0,
            'countdown_str': 'No Deadline',
            'end_date_iso': None
        }

    now = get_ist_now()
    diff = notice.end_date - now
    diff_seconds = int(diff.total_seconds())

    # 1. Deadline has passed (diff_seconds <= 0)
    if diff_seconds <= 0:
        # Check if still within the 1 extra day (24 hours / 86400 seconds) after deadline
        is_extra_day = (diff_seconds >= -86400)
        return {
            'has_deadline': True,
            'is_expired': True,
            'is_extra_day': is_extra_day,
            'is_completely_expired': not is_extra_day,
            'is_urgent': is_extra_day,  # Still urgent/visible during the 1 extra day
            'total_seconds': 0,
            'days': 0,
            'hours': 0,
            'minutes': 0,
            'seconds': 0,
            'countdown_str': 'Assignment Date Over • Submission Closed',
            'end_date_iso': notice.end_date.strftime('%Y-%m-%dT%H:%M:%S')
        }

    # 2. Deadline is active in future
    days = diff_seconds // 86400
    hours = (diff_seconds % 86400) // 3600
    minutes = (diff_seconds % 3600) // 60
    seconds = diff_seconds % 60

    is_urgent = (diff_seconds <= (2 * 86400)) or (getattr(notice, 'priority', 'Normal') == 'Urgent')
    countdown_str = f"{days:02d}d : {hours:02d}h : {minutes:02d}m : {seconds:02d}s Left"

    return {
        'has_deadline': True,
        'is_expired': False,
        'is_extra_day': False,
        'is_completely_expired': False,
        'is_urgent': is_urgent,
        'total_seconds': diff_seconds,
        'days': days,
        'hours': hours,
        'minutes': minutes,
        'seconds': seconds,
        'countdown_str': countdown_str,
        'end_date_iso': notice.end_date.strftime('%Y-%m-%dT%H:%M:%S')
    }


def get_student_urgent_notices(student):
    """
    Returns urgent notices for this student:
    - Notices with active deadlines <= 2 days (live countdown)
    - Notices whose deadline has passed but are within the extra 1 day (timer stopped, 'Assignment Date Over • Submission Closed')
    - Direct or Priority='Urgent' notices (such as Device Registration Alerts)
    After the extra 1 day (> 24 hours past end_date), notice is completely excluded from the urgent banner.
    """
    all_notices = get_student_notices(student)
    urgent_items = []
    for notice in all_notices:
        try:
            info = calculate_deadline_info(notice)
            notice.deadline_info = info
            if info.get('has_deadline'):
                # Phase 1: Within 2 days of deadline -> Active live countdown
                if not info.get('is_expired') and info.get('is_urgent'):
                    urgent_items.append(notice)
                # Phase 2: Deadline passed, within 1 extra day -> Stopped timer with 'Assignment Date Over • Submission Closed'
                elif info.get('is_extra_day'):
                    urgent_items.append(notice)
                # Phase 3: > 1 day past deadline -> Excluded completely!
            elif getattr(notice, 'priority', 'Normal') == 'Urgent':
                urgent_items.append(notice)
        except Exception:
            if getattr(notice, 'priority', 'Normal') == 'Urgent':
                urgent_items.append(notice)
    return urgent_items


def delete_notification(notification_id, requester_role, requester_id):
    """
    Deletes a notification from DB and removes its attachment file if any.
    Admin can delete any notification.
    Faculty can only delete notifications created by themselves.
    """
    notice = Notification.query.get(notification_id)
    if not notice:
        return False, "Notification not found"

    if requester_role == 'Faculty' and (notice.posted_by_role != 'Faculty' or notice.faculty_id != requester_id):
        return False, "Unauthorized to delete this notification"

    # Remove attachment file if exists
    if notice.photo_file:
        try:
            file_path = os.path.join(current_app.root_path, 'uploads', 'notifications', notice.photo_file)
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            current_app.logger.warning(f"Could not remove notification file: {e}")

    try:
        db.session.delete(notice)
        db.session.commit()
        return True, "Notification deleted successfully"
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"[delete_notification Error] {e}")
        return False, f"Could not delete notification: {str(e)}"


def toggle_notification_status(notification_id):
    """Toggles active/inactive status of a notification (Admin only)."""
    notice = Notification.query.get(notification_id)
    if not notice:
        return False, "Notification not found"

    try:
        notice.is_active = not notice.is_active
        db.session.commit()
        return True, f"Notification marked as {'Active' if notice.is_active else 'Inactive'}"
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"[toggle_notification_status Error] {e}")
        return False, f"Could not update notification status: {str(e)}"


def format_time_ago(dt):
    """Converts a datetime into a clean, relative time string (e.g. 5m ago, 2h ago, Yesterday)."""
    if not dt:
        return ""
    now = datetime.utcnow()
    diff = now - dt
    seconds = int(diff.total_seconds())
    if seconds < 0:
        return "Just now"
    if seconds < 60:
        return "Just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days}d ago"
    return dt.strftime("%d %b")


def get_user_applicable_notices(user_role, user_id):
    """Returns active notifications applicable to the given user role and id."""
    role = (user_role or '').capitalize()
    if role == 'Admin':
        return get_admin_feed_notices(filter_by_cycle=True)
    elif role == 'Faculty':
        return get_faculty_notices(filter_by_cycle=True)
    elif role == 'Student':
        student = Student.query.get(user_id)
        if not student:
            return []
        return get_student_notices(student)
    return []


def get_user_notifications_feed(user_role, user_id, limit=8):
    """
    Returns the notification feed and unread count for dynamic bell badge & dropdown.
    WhatsApp/Instagram style seen tracking.
    """
    notices = get_user_applicable_notices(user_role, user_id)
    if not notices:
        return {
            "unread_count": 0,
            "notifications": []
        }

    notice_ids = [n.id for n in notices]
    read_ids = set()
    try:
        read_rows = NotificationRead.query.filter(
            NotificationRead.user_role == user_role,
            NotificationRead.user_id == user_id,
            NotificationRead.notification_id.in_(notice_ids)
        ).all()
        read_ids = {r.notification_id for r in read_rows}
    except Exception:
        db.session.rollback()
        ensure_notification_reads_table()
        try:
            read_rows = NotificationRead.query.filter(
                NotificationRead.user_role == user_role,
                NotificationRead.user_id == user_id,
                NotificationRead.notification_id.in_(notice_ids)
            ).all()
            read_ids = {r.notification_id for r in read_rows}
        except Exception:
            db.session.rollback()
            read_ids = set()

    unread_count = sum(1 for nid in notice_ids if nid not in read_ids)

    # Prepare top notices
    feed_items = []
    for n in notices[:limit]:
        is_read = n.id in read_ids
        feed_items.append({
            "id": n.id,
            "title": n.title,
            "message": n.message[:130] + ("..." if len(n.message) > 130 else ""),
            "full_message": n.message,
            "category": n.category,
            "priority": n.priority,
            "posted_by_role": n.posted_by_role,
            "posted_by_name": n.author_name,
            "photo_file": n.photo_file,
            "file_type": n.file_type,
            "is_read": is_read,
            "time_ago": format_time_ago(n.created_at),
            "created_at": n.created_at.strftime("%d %b %Y, %I:%M %p") if n.created_at else None,
            "start_date": n.start_date.strftime("%d %b %Y, %I:%M %p") if n.start_date else None,
            "end_date": n.end_date.strftime("%d %b %Y, %I:%M %p") if n.end_date else None,
        })

    return {
        "unread_count": unread_count,
        "notifications": feed_items
    }


def mark_notification_as_read(notification_id, user_role, user_id):
    """Marks a single notification as read for the user."""
    if not notification_id or not user_role or not user_id:
        return False

    existing = NotificationRead.query.filter_by(
        notification_id=notification_id,
        user_role=user_role,
        user_id=user_id
    ).first()

    if not existing:
        new_read = NotificationRead(
            notification_id=notification_id,
            user_role=user_role,
            user_id=user_id,
            read_at=datetime.utcnow()
        )
        db.session.add(new_read)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
    return True


def mark_all_notifications_as_read(user_role, user_id):
    """Marks all currently applicable active notifications as read for the user."""
    try:
        notices = get_user_applicable_notices(user_role, user_id)
        if not notices:
            return 0

        notice_ids = [n.id for n in notices]
        existing_reads = set()
        try:
            read_rows = NotificationRead.query.filter(
                NotificationRead.user_role == user_role,
                NotificationRead.user_id == user_id,
                NotificationRead.notification_id.in_(notice_ids)
            ).all()
            existing_reads = {r.notification_id for r in read_rows}
        except Exception as read_err:
            db.session.rollback()
            current_app.logger.warning(f"[NotificationRead Error] {read_err}")
            return 0

        added_count = 0
        now = datetime.utcnow()
        for nid in notice_ids:
            if nid not in existing_reads:
                db.session.add(NotificationRead(
                    notification_id=nid,
                    user_role=user_role,
                    user_id=user_id,
                    read_at=now
                ))
                added_count += 1

        if added_count > 0:
            db.session.commit()
        return added_count
    except Exception as e:
        db.session.rollback()
        current_app.logger.warning(f"[mark_all_notifications_as_read Error] {e}")
        return 0

