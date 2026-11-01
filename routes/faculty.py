"""
CampusSync ERP - Faculty Routes
===============================
File: routes/faculty.py
"""

import os
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from services.faculty_service import (
    authenticate_faculty,
    get_faculty_model,
    get_faculty_dashboard_data,
    get_faculty_assigned_subjects,
    change_faculty_password,
    get_faculty_marks_students,
    get_faculty_assigned_students
)
from services.academic_history_service import save_detailed_internal_mark, save_bulk_internal_marks
from services.attendance_service import (
    get_subject_attendance_summary, verify_attendance_ready,
    get_lecture_session_attendance, save_lecture_session_attendance,
    start_faculty_qr_session, get_faculty_qr_live_roster,
    resolve_faculty_security_alert, stop_faculty_qr_session, extend_faculty_qr_session
)
from datetime import datetime
from services.export_marks_service import (
    generate_internal_marks_excel,
    generate_internal_marks_pdf,
    sanitize_filename_component
)
from utils.decorators import faculty_required
from services.academic_service import get_academic_settings

faculty_bp = Blueprint('faculty', __name__)


# --- Faculty Context Processor ---
@faculty_bp.context_processor
def inject_current_faculty():
    """Injects current logged-in Faculty object into all faculty templates."""
    if "faculty_id" in session:
        from flask import g
        if not hasattr(g, 'current_faculty'):
            g.current_faculty = get_faculty_model(session["faculty_id"])
        return dict(current_faculty=g.current_faculty)
    return dict(current_faculty=None)


# --- Faculty Login Route ---
@faculty_bp.route('/auth/faculty-login', methods=['GET', 'POST'])
@faculty_bp.route('/faculty/login', methods=['GET', 'POST'], endpoint='faculty_login')
def faculty_login():
    """
    Handles Faculty Login logic:
    - GET: Displays faculty login page.
    - POST: Verifies email & password, validates active status, sets session.
      Redirects to /faculty/change-password if password_changed is False.
    """
    # Check if faculty is already logged in
    if "faculty_id" in session:
        faculty = get_faculty_model(session["faculty_id"])
        if faculty and faculty.status == 'Active':
            if not faculty.password_changed:
                return redirect(url_for('faculty_change_password'))
            return redirect(url_for('faculty_dashboard'))
        else:
            session.pop("faculty_id", None)

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')

        faculty, error = authenticate_faculty(email, password)
        if error:
            return render_template('auth/faculty-login.html', error=error)

        # Set faculty session (isolated from student_id and admin_id)
        session.permanent = True
        session["faculty_id"] = faculty.id
        session["user_role"] = "faculty"
        session["last_active"] = datetime.utcnow().timestamp()

        # Check first-login requirement
        if not faculty.password_changed:
            flash("For security, please change your default password before continuing.", "warning")
            return redirect(url_for('faculty_change_password'))

        return redirect(url_for('faculty_dashboard'))

    success_msg = request.args.get('success')
    return render_template('auth/faculty-login.html', success=success_msg)


# --- Faculty Logout Route ---
@faculty_bp.route('/faculty/logout', endpoint='faculty_logout')
def faculty_logout():
    """
    Handles Faculty Logout:
    - Clears faculty session.
    - Redirects back to Faculty Login page.
    """
    session.clear()
    return redirect(url_for('faculty_login'))


# --- Faculty Forgot Password Route ---
@faculty_bp.route('/faculty/forgot-password', methods=['GET', 'POST'], endpoint='faculty_forgot_password')
def faculty_forgot_password():
    """
    Handles Faculty Password Reset OTP request:
    - GET: Shows forgot password email form.
    - POST: Generates hashed OTP in DB and sends OTP email.
    """
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        from services.password_reset_service import request_password_reset_otp
        success, msg, target_email = request_password_reset_otp('faculty', email)

        if success:
            return redirect(url_for('faculty_reset_password', email=target_email))
        else:
            return render_template(
                'auth/forgot-password.html',
                portal_name='Faculty',
                action_url=url_for('faculty_forgot_password'),
                login_url=url_for('faculty_login'),
                email=email,
                error=msg
            )

    preset_email = request.args.get('email', '')
    return render_template(
        'auth/forgot-password.html',
        portal_name='Faculty',
        action_url=url_for('faculty_forgot_password'),
        login_url=url_for('faculty_login'),
        email=preset_email
    )


# --- Faculty Reset Password Route ---
@faculty_bp.route('/faculty/reset-password', methods=['GET', 'POST'], endpoint='faculty_reset_password')
def faculty_reset_password():
    """
    Handles Faculty OTP verification and password update:
    - GET: Shows reset password form.
    - POST: Verifies hashed OTP from DB, updates password, redirects to login.
    """
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        otp = request.form.get('otp', '').strip()
        new_pwd = request.form.get('new_password', '')
        confirm_pwd = request.form.get('confirm_password', '')

        from services.password_reset_service import verify_otp_and_reset_password
        success, msg = verify_otp_and_reset_password('faculty', email, otp, new_pwd, confirm_pwd)

        if success:
            return redirect(url_for('faculty_login', success=msg))
        else:
            return render_template(
                'auth/reset-password.html',
                portal_name='Faculty',
                action_url=url_for('faculty_reset_password'),
                resend_url=url_for('faculty_forgot_password', email=email),
                login_url=url_for('faculty_login'),
                email=email,
                error=msg
            )

    email = request.args.get('email', '')
    return render_template(
        'auth/reset-password.html',
        portal_name='Faculty',
        action_url=url_for('faculty_reset_password'),
        resend_url=url_for('faculty_forgot_password', email=email),
        login_url=url_for('faculty_login'),
        email=email
    )


# --- Faculty Dashboard Route ---
@faculty_bp.route('/faculty/dashboard', endpoint='faculty_dashboard')
@faculty_required
def faculty_dashboard():
    """
    Protected Faculty Dashboard route:
    - Loads real logged-in faculty record from MySQL.
    - Computes real statistics (assigned subjects, divisions, department).
    - Queries assigned subjects joined with Subject table.
    """
    faculty_id = session.get("faculty_id")
    from flask import g
    faculty = getattr(g, 'current_faculty', None) or get_faculty_model(faculty_id)
    dashboard_data = get_faculty_dashboard_data(faculty_id)

    return render_template(
        'faculty/dashboard.html',
        faculty=faculty,
        dashboard_data=dashboard_data,
        active_page='dashboard'
    )


# --- Faculty Profile Route ---
@faculty_bp.route('/faculty/profile', endpoint='faculty_profile')
@faculty_required
def faculty_profile():
    """
    Protected Faculty Profile route:
    - Displays full personal, professional, and contact details from MySQL.
    """
    faculty_id = session.get("faculty_id")
    faculty = get_faculty_model(faculty_id)
    assigned_subjects = get_faculty_assigned_subjects(faculty_id)

    return render_template(
        'faculty/profile.html',
        faculty=faculty,
        assigned_subjects=assigned_subjects,
        active_page='profile'
    )


# --- Faculty Assigned Subjects Route ---
@faculty_bp.route('/faculty/subjects', endpoint='faculty_subjects')
@faculty_required
def faculty_subjects():
    """
    Protected Faculty Subjects route:
    - Displays all assigned subjects and divisions for current logged-in faculty.
    """
    faculty_id = session.get("faculty_id")
    faculty = get_faculty_model(faculty_id)
    assigned_subjects = get_faculty_assigned_subjects(faculty_id)

    return render_template(
        'faculty/subjects.html',
        faculty=faculty,
        assigned_subjects=assigned_subjects,
        active_page='subjects'
    )


# --- Faculty Change Password Route ---
@faculty_bp.route('/faculty/change-password', methods=['GET', 'POST'], endpoint='faculty_change_password')
@faculty_required
def faculty_change_password():
    """
    Protected Change Password route:
    - Handles first-login mandatory password change and voluntary updates.
    - Hashes new password with Werkzeug and updates password_changed = True.
    """
    faculty_id = session.get("faculty_id")
    faculty = get_faculty_model(faculty_id)

    if request.method == 'POST':
        current_password = request.form.get('current_password', '')
        new_password = request.form.get('new_password', '')
        confirm_password = request.form.get('confirm_password', '')

        success, message = change_faculty_password(
            faculty_id, current_password, new_password, confirm_password
        )

        if not success:
            return render_template(
                'faculty/change-password.html',
                faculty=faculty,
                error=message,
                active_page='change_password'
            )

        flash("Password changed successfully.", "success")
        return redirect(url_for('faculty_dashboard'))

    return render_template(
        'faculty/change-password.html',
        faculty=faculty,
        active_page='change_password'
    )


# --- Placeholder Faculty Routes (Safeguards url_for backward compatibility) ---
@faculty_bp.route('/faculty/qr', endpoint='faculty_qr')
@faculty_required
def faculty_qr():
    return redirect(url_for('faculty_dashboard'))

@faculty_bp.route('/faculty/register', endpoint='faculty_register')
@faculty_required
def faculty_register():
    return redirect(url_for('faculty_dashboard'))

@faculty_bp.route('/faculty/marks', endpoint='faculty_marks')
@faculty_required
def faculty_marks():
    """
    Faculty Internal Marks Route:
    - Displays subjects/divisions assigned to logged-in faculty.
    - Provides filters: Academic Year, Semester, Subject, Division.
    - Queries student list filtered by subject/division and StudentSubject mapping.
    - Shows marks status and allows detailed HNGU assessment component entry.
    """
    faculty_id = session.get("faculty_id")
    faculty = get_faculty_model(faculty_id)
    assigned_subjects = get_faculty_assigned_subjects(faculty_id)

    academic = get_academic_settings()
    current_year = academic.academic_year if academic else "2026-27"
    available_years = [current_year]
    selected_year = request.args.get("academic_year", current_year).strip()

    # Parse semester filter
    selected_semester = request.args.get("semester")
    if selected_semester:
        try:
            selected_semester = int(selected_semester)
        except ValueError:
            selected_semester = None

    # Parse subject filter
    selected_subject_id = request.args.get("subject_id")
    if selected_subject_id:
        try:
            selected_subject_id = int(selected_subject_id)
        except ValueError:
            selected_subject_id = None

    selected_division = request.args.get("division", "").strip().upper()

    # Default to first assigned subject and division if none selected
    if not selected_subject_id and assigned_subjects:
        selected_subject_id = assigned_subjects[0]["subject_id"]
        selected_division = assigned_subjects[0]["division"]
        selected_semester = assigned_subjects[0]["semester"]
    elif selected_subject_id and not selected_division and assigned_subjects:
        # Find matching division for selected subject
        for asub in assigned_subjects:
            if asub["subject_id"] == selected_subject_id:
                selected_division = asub["division"]
                if not selected_semester:
                    selected_semester = asub["semester"]
                break

    student_data = None
    error_msg = None

    if selected_subject_id and selected_division:
        student_data, error_msg = get_faculty_marks_students(
            faculty_id=faculty_id,
            subject_id=selected_subject_id,
            division=selected_division,
            semester=selected_semester,
            academic_year=selected_year
        )

    return render_template(
        'faculty/marks.html',
        faculty=faculty,
        assigned_subjects=assigned_subjects,
        available_years=available_years,
        selected_year=selected_year,
        selected_semester=selected_semester,
        selected_subject_id=selected_subject_id,
        selected_division=selected_division,
        student_data=student_data,
        error=error_msg,
        active_page='marks'
    )


@faculty_bp.route('/faculty/marks/save', methods=['POST'], endpoint='faculty_marks_save')
@faculty_required
def faculty_marks_save():
    """
    API endpoint for saving detailed internal marks for a student.
    Validates faculty assignment, component bounds, non-negative bounds, and total internal marks.
    """
    faculty_id = session.get("faculty_id")

    if request.is_json:
        data = request.get_json() or {}
    else:
        data = request.form.to_dict()

    try:
        student_id = int(data.get("student_id", 0))
        subject_id = int(data.get("subject_id", 0))
        semester = int(data.get("semester", 0))
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid IDs or semester value."}), 400

    academic_year = data.get("academic_year", "2026-27").strip()
    division = data.get("division", "").strip().upper()

    # Authorization check
    from models import FacultySubjectAssignment
    assignment = FacultySubjectAssignment.query.filter_by(
        faculty_id=faculty_id,
        subject_id=subject_id,
        division=division,
        status='Active'
    ).first()

    if not assignment:
        return jsonify({"success": False, "message": "Access Denied: You are not assigned to this subject/division."}), 403

    record, err = save_detailed_internal_mark(
        student_id=student_id,
        subject_id=subject_id,
        semester=semester,
        academic_year=academic_year,
        data_dict=data
    )

    if err:
        return jsonify({"success": False, "message": err}), 400

    return jsonify({
        "success": True,
        "message": "Internal marks updated successfully.",
        "marks_obtained": record.marks_obtained,
        "max_marks": record.max_marks
    })


@faculty_bp.route('/faculty/marks/bulk-save', methods=['POST'], endpoint='faculty_marks_bulk_save')
@faculty_required
def faculty_marks_bulk_save():
    """
    API endpoint for saving bulk internal marks for multiple students.
    Validates server-side faculty authorization, component range constraints,
    and performs atomic database transaction.
    """
    faculty_id = session.get("faculty_id")

    payload = request.get_json() or {}
    try:
        subject_id = int(payload.get("subject_id", 0))
        semester = int(payload.get("semester", 0))
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid subject or semester ID."}), 400

    division = payload.get("division", "").strip().upper()
    academic_year = payload.get("academic_year", "2026-27").strip()
    save_type = payload.get("save_type", "draft").strip().lower()
    if save_type not in ["draft", "final"]:
        save_type = "draft"
    marks_list = payload.get("marks", [])

    if not marks_list:
        return jsonify({"success": False, "message": "No student marks data submitted."}), 400

    # Backend Attendance Protection Check
    is_att_ready, att_err = verify_attendance_ready(
        subject_id=subject_id,
        semester=semester,
        academic_year=academic_year,
        division=division,
        marks_list=marks_list
    )
    if not is_att_ready:
        return jsonify({"success": False, "message": att_err}), 400

    result, err = save_bulk_internal_marks(
        faculty_id=faculty_id,
        subject_id=subject_id,
        division=division,
        semester=semester,
        academic_year=academic_year,
        marks_list=marks_list,
        save_type=save_type
    )

    if err:
        return jsonify({"success": False, "message": err}), 400

    return jsonify({
        "success": True,
        "message": result["message"],
        "saved_count": result["saved_count"]
    })


@faculty_bp.route('/faculty/marks/fetch-attendance', methods=['POST'], endpoint='faculty_marks_fetch_attendance')
@faculty_required
def faculty_marks_fetch_attendance():
    """
    API endpoint for retrieving subject-specific student attendance percentages
    and converting them to HNGU internal assessment Attendance /5 scores.
    Validates server-side faculty assignment authorization.
    """
    faculty_id = session.get("faculty_id")

    payload = request.get_json() or {}
    try:
        subject_id = int(payload.get("subject_id", 0))
        semester = int(payload.get("semester", 0))
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid subject or semester ID."}), 400

    division = payload.get("division", "").strip().upper()
    academic_year = payload.get("academic_year", "2026-27").strip()
    student_ids = payload.get("student_ids", [])

    if not student_ids:
        return jsonify({"success": False, "message": "No students selected."}), 400

    # Authorization Check
    from models import FacultySubjectAssignment
    assignment = FacultySubjectAssignment.query.filter_by(
        faculty_id=faculty_id,
        subject_id=subject_id,
        division=division,
        status='Active'
    ).first()

    if not assignment:
        return jsonify({"success": False, "message": "Access Denied: You are not assigned to this subject/division."}), 403

    summary_map = get_subject_attendance_summary(
        student_ids=student_ids,
        subject_id=subject_id,
        semester=semester,
        academic_year=academic_year,
        division=division
    )

    return jsonify({
        "success": True,
        "attendance": summary_map
    })

@faculty_bp.route('/faculty/attendance', endpoint='faculty_attendance')
@faculty_required
def faculty_attendance():
    """
    Faculty Manual Attendance Register Route:
    - Filters: Academic Year, Subject & Division, Date (default today), Lecture Session No.
    - Pre-fills student attendance statuses if draft or submitted session already exists.
    """
    faculty_id = session.get("faculty_id")
    faculty = get_faculty_model(faculty_id)
    assigned_subjects = get_faculty_assigned_subjects(faculty_id)

    academic = get_academic_settings()
    current_year = academic.academic_year if academic else "2026-27"
    available_years = [current_year]
    selected_year = request.args.get("academic_year", current_year).strip()

    selected_semester = request.args.get("semester")
    if selected_semester:
        try:
            selected_semester = int(selected_semester)
        except ValueError:
            selected_semester = None

    selected_subject_id = request.args.get("subject_id")
    if selected_subject_id:
        try:
            selected_subject_id = int(selected_subject_id)
        except ValueError:
            selected_subject_id = None

    selected_division = request.args.get("division", "").strip().upper()

    # Default to first assigned subject/division if none selected
    if not selected_subject_id and assigned_subjects:
        selected_subject_id = assigned_subjects[0]["subject_id"]
        selected_division = assigned_subjects[0]["division"]
        selected_semester = assigned_subjects[0]["semester"]
    elif selected_subject_id and not selected_division and assigned_subjects:
        for asub in assigned_subjects:
            if asub["subject_id"] == selected_subject_id:
                selected_division = asub["division"]
                if not selected_semester:
                    selected_semester = asub["semester"]
                break

    today_str = datetime.now().strftime("%Y-%m-%d")
    selected_date = request.args.get("lecture_date", today_str).strip()
    session_type = request.args.get("session_type", "Lecture").strip()
    start_time = request.args.get("start_time", "11:00").strip()
    end_time = request.args.get("end_time", "12:00").strip()

    import re
    raw_lec_no = request.args.get("lecture_no", "").strip()
    if raw_lec_no:
        m = re.match(r'(Lecture|Lab)\s*\(([\d:]+)\s*-\s*([\d:]+)\)', raw_lec_no)
        if m:
            if not request.args.get("session_type"):
                session_type = m.group(1)
            if not request.args.get("start_time"):
                start_time = m.group(2)
            if not request.args.get("end_time"):
                end_time = m.group(3)
        selected_lecture_no = raw_lec_no
    else:
        selected_lecture_no = f"{session_type} ({start_time} - {end_time})"

    from services.attendance_service import validate_session_time_and_conflict
    is_valid_timing, timing_msg = validate_session_time_and_conflict(
        semester=selected_semester,
        division=selected_division,
        lecture_date=selected_date,
        start_time=start_time,
        end_time=end_time,
        subject_id=selected_subject_id
    )

    student_data = None
    existing_session = None
    error_msg = timing_msg if not is_valid_timing else None
    active_qr_session = None
    qr_remaining_seconds = 0

    if selected_subject_id and selected_division:
        student_data, error_msg = get_faculty_assigned_students(
            faculty_id=faculty_id,
            subject_id=selected_subject_id,
            division=selected_division,
            semester=selected_semester,
            academic_year=selected_year
        )

        # Check if there is currently an active QR attendance session running for this lecture
        from models import LectureAttendanceSession, CollegeSetting, db
        from datetime import datetime as dt
        try:
            l_date = dt.strptime(selected_date.strip(), "%Y-%m-%d").date()
        except (ValueError, TypeError, AttributeError):
            l_date = dt.today().date()

        active_qr = LectureAttendanceSession.query.filter_by(
            subject_id=selected_subject_id,
            semester=selected_semester,
            division=selected_division,
            academic_year=selected_year,
            lecture_date=l_date,
            lecture_no=selected_lecture_no,
            is_qr_active=1
        ).order_by(LectureAttendanceSession.id.desc()).first()

        if active_qr:
            now_utc = dt.utcnow()
            if active_qr.status == 'Submitted' or (active_qr.qr_session_expires_at and now_utc > active_qr.qr_session_expires_at):
                active_qr.is_qr_active = 0
                db.session.commit()
            else:
                college = CollegeSetting.query.first()
                campus_lat = float(college.campus_latitude) if college and college.campus_latitude else 24.15953750
                campus_lng = float(college.campus_longitude) if college and college.campus_longitude else 72.40295313
                campus_radius = int(college.campus_radius_meters) if college and college.campus_radius_meters else 800
                qr_remaining_seconds = max(0, int((active_qr.qr_session_expires_at - now_utc).total_seconds())) if active_qr.qr_session_expires_at else 600

                active_qr_session = {
                    "id": active_qr.id,
                    "token": active_qr.qr_session_token,
                    "expires_at": active_qr.qr_session_expires_at.isoformat() if active_qr.qr_session_expires_at else None,
                    "remaining_seconds": qr_remaining_seconds,
                    "campus_lat": campus_lat,
                    "campus_lng": campus_lng,
                    "campus_radius": campus_radius
                }

        if student_data and student_data.get("students"):
            existing_session = get_lecture_session_attendance(
                faculty_id=faculty_id,
                subject_id=selected_subject_id,
                division=selected_division,
                semester=selected_semester,
                academic_year=selected_year,
                lecture_date=selected_date,
                lecture_no=selected_lecture_no
            )

            # If lecture is already Final Submitted & Locked, strictly clear and deactivate any QR session
            if existing_session and existing_session.get("status") == 'Submitted':
                active_qr_session = None
                qr_remaining_seconds = 0
                if active_qr and active_qr.is_qr_active:
                    active_qr.is_qr_active = 0
                    active_qr.qr_session_expires_at = dt.utcnow()
                    db.session.commit()

            # Pre-fill status for each student if session exists (default to Absent so unscanned students do not show Present)
            if existing_session and existing_session.get("statuses"):
                session_statuses = existing_session["statuses"]
                for st in student_data["students"]:
                    st["att_status"] = session_statuses.get(st["id"], "Absent")
            else:
                for st in student_data["students"]:
                    st["att_status"] = "Absent"

    pending_alert = None
    session_id_to_check = None
    if active_qr_session:
        session_id_to_check = active_qr_session.get("id") if isinstance(active_qr_session, dict) else getattr(active_qr_session, "id", None)
    elif existing_session and isinstance(existing_session, dict):
        session_id_to_check = existing_session.get("session_id") or existing_session.get("id")

    from models import AttendanceSecurityAlert, LectureAttendanceSession
    if session_id_to_check:
        pending_alert = AttendanceSecurityAlert.query.filter_by(
            session_id=session_id_to_check,
            faculty_action='PENDING'
        ).order_by(AttendanceSecurityAlert.id.desc()).first()

    if not pending_alert and selected_subject_id and selected_semester and selected_division:
        sess_ids = [s.id for s in LectureAttendanceSession.query.filter_by(
            subject_id=selected_subject_id,
            semester=selected_semester,
            division=selected_division,
            academic_year=selected_year
        ).order_by(LectureAttendanceSession.id.desc()).limit(5).all()]
        if sess_ids:
            pending_alert = AttendanceSecurityAlert.query.filter(
                AttendanceSecurityAlert.session_id.in_(sess_ids),
                AttendanceSecurityAlert.faculty_action == 'PENDING'
            ).order_by(AttendanceSecurityAlert.id.desc()).first()
            if not session_id_to_check and pending_alert:
                session_id_to_check = pending_alert.session_id

    return render_template(
        'faculty/attendance_manual.html',
        faculty=faculty,
        assigned_subjects=assigned_subjects,
        available_years=available_years,
        selected_year=selected_year,
        selected_semester=selected_semester,
        selected_subject_id=selected_subject_id,
        selected_division=selected_division,
        selected_date=selected_date,
        selected_lecture_no=selected_lecture_no,
        selected_session_type=session_type,
        selected_start_time=start_time,
        selected_end_time=end_time,
        college=CollegeSetting.query.first(),
        student_data=student_data,
        existing_session=existing_session,
        active_qr_session=active_qr_session,
        qr_remaining_seconds=qr_remaining_seconds,
        pending_alert=pending_alert,
        poll_session_id=session_id_to_check,
        error=error_msg,
        active_page='attendance'
    )


@faculty_bp.route('/faculty/attendance/save', methods=['POST'], endpoint='faculty_attendance_save')
@faculty_required
def faculty_attendance_save():
    """
    API endpoint for saving attendance register (Draft or Final Submit).
    """
    faculty_id = session.get("faculty_id")
    payload = request.get_json() or {}

    try:
        subject_id = int(payload.get("subject_id", 0))
        semester = int(payload.get("semester", 0))
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Invalid subject or semester ID."}), 400

    division = payload.get("division", "").strip().upper()
    academic_year = payload.get("academic_year", "2026-27").strip()
    lecture_date = payload.get("lecture_date", "").strip()
    lecture_no = payload.get("lecture_no", "Lecture 1").strip()
    action_type = payload.get("action_type", "draft").strip().lower()
    student_statuses = payload.get("statuses", {})

    if not student_statuses:
        return jsonify({"success": False, "message": "No student attendance statuses submitted."}), 400

    session_type = payload.get("session_type", "Lecture").strip()
    start_time = payload.get("start_time")
    end_time = payload.get("end_time")
    if start_time and end_time:
        lecture_no = f"{session_type} ({start_time} - {end_time})"

    success, msg = save_lecture_session_attendance(
        faculty_id=faculty_id,
        subject_id=subject_id,
        division=division,
        semester=semester,
        academic_year=academic_year,
        lecture_date=lecture_date,
        lecture_no=lecture_no,
        student_statuses=student_statuses,
        action_type=action_type,
        session_type=session_type,
        start_time=start_time,
        end_time=end_time
    )

    if not success:
        return jsonify({"success": False, "message": msg}), 400

    return jsonify({
        "success": True,
        "message": msg,
        "action_type": action_type
    })


@faculty_bp.route('/faculty/attendance/summary-pdf', endpoint='faculty_attendance_summary_pdf')
@faculty_required
def faculty_attendance_summary_pdf():
    """
    Downloads the Attendance Module Technical Summary PDF Report.
    """
    pdf_path = os.path.join(current_app.root_path, "documentation", "CampusSync_Attendance_Module_Summary_Report.pdf")
    if not os.path.exists(pdf_path):
        flash("Documentation PDF not found.", "warning")
        return redirect(url_for('faculty.faculty_attendance'))
    return send_file(
        pdf_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="CampusSync_Attendance_Module_Summary_Report.pdf"
    )


@faculty_bp.route('/faculty/results', endpoint='faculty_results')
@faculty_required
def faculty_results():
    return redirect(url_for('faculty_dashboard'))

@faculty_bp.route('/faculty/lecture', endpoint='faculty_lecture')
@faculty_required
def faculty_lecture():
    return redirect(url_for('faculty_dashboard'))

@faculty_bp.route('/faculty/activities', endpoint='faculty_activities')
@faculty_required
def faculty_activities():
    return redirect(url_for('faculty_dashboard'))


# --- Faculty Internal Marks Export Routes ---

@faculty_bp.route('/faculty/marks/export/excel', endpoint='faculty_marks_export_excel')
@faculty_bp.route('/faculty/internal-marks/export/excel', endpoint='faculty_internal_marks_export_excel')
@faculty_required
def faculty_marks_export_excel():
    """
    Exports Faculty Internal Marks as a downloadable Excel (.xlsx) spreadsheet.
    Enforces logged-in faculty authorization and filters by academic_year, semester, subject_id, division.
    """
    faculty_id = session.get("faculty_id")
    faculty = get_faculty_model(faculty_id)
    faculty_name = faculty.full_name if faculty else "Faculty"

    academic_year = request.args.get("academic_year", "2026-27").strip()
    division = request.args.get("division", "").strip().upper()

    try:
        subject_id = int(request.args.get("subject_id", 0))
    except (ValueError, TypeError):
        subject_id = 0

    try:
        semester = int(request.args.get("semester", 0))
        if semester <= 0:
            semester = None
    except (ValueError, TypeError):
        semester = None

    if not subject_id or not division:
        flash("Please select Subject and Division first.", "warning")
        return redirect(url_for('faculty_marks'))

    # Backend Attendance Protection Check
    is_att_ready, att_err = verify_attendance_ready(
        subject_id=subject_id,
        semester=semester,
        academic_year=academic_year,
        division=division
    )
    if not is_att_ready:
        flash(att_err, "danger")
        return redirect(url_for('faculty_marks', academic_year=academic_year, subject_id=subject_id, division=division))

    data_dict, err = get_faculty_marks_students(
        faculty_id=faculty_id,
        subject_id=subject_id,
        division=division,
        semester=semester,
        academic_year=academic_year
    )

    if err:
        flash(err, "danger")
        return redirect(url_for('faculty_marks', academic_year=academic_year, subject_id=subject_id, division=division))

    if not data_dict or not data_dict.get("students"):
        flash("No student records found for the selected filters.", "warning")
        return redirect(url_for('faculty_marks', academic_year=academic_year, subject_id=subject_id, division=division))

    excel_buffer = generate_internal_marks_excel(data_dict, faculty_name=faculty_name)

    sub_code = sanitize_filename_component(data_dict["subject"]["subject_code"])
    div_str = sanitize_filename_component(data_dict["division"])
    ay_str = sanitize_filename_component(data_dict["academic_year"])
    filename = f"CampusSync_Internal_Marks_{sub_code}_{div_str}_{ay_str}.xlsx"

    return send_file(
        excel_buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename
    )


@faculty_bp.route('/faculty/marks/export/pdf', endpoint='faculty_marks_export_pdf')
@faculty_bp.route('/faculty/internal-marks/export/pdf', endpoint='faculty_internal_marks_export_pdf')
@faculty_required
def faculty_marks_export_pdf():
    """
    Exports Faculty Internal Marks as a downloadable PDF document.
    Enforces logged-in faculty authorization and filters by academic_year, semester, subject_id, division.
    """
    faculty_id = session.get("faculty_id")
    faculty = get_faculty_model(faculty_id)
    faculty_name = faculty.full_name if faculty else "Faculty"

    academic_year = request.args.get("academic_year", "2026-27").strip()
    division = request.args.get("division", "").strip().upper()

    try:
        subject_id = int(request.args.get("subject_id", 0))
    except (ValueError, TypeError):
        subject_id = 0

    try:
        semester = int(request.args.get("semester", 0))
        if semester <= 0:
            semester = None
    except (ValueError, TypeError):
        semester = None

    if not subject_id or not division:
        flash("Please select Subject and Division first.", "warning")
        return redirect(url_for('faculty_marks'))

    # Backend Attendance Protection Check
    is_att_ready, att_err = verify_attendance_ready(
        subject_id=subject_id,
        semester=semester,
        academic_year=academic_year,
        division=division
    )
    if not is_att_ready:
        flash(att_err, "danger")
        return redirect(url_for('faculty_marks', academic_year=academic_year, subject_id=subject_id, division=division))

    data_dict, err = get_faculty_marks_students(
        faculty_id=faculty_id,
        subject_id=subject_id,
        division=division,
        semester=semester,
        academic_year=academic_year
    )

    if err:
        flash(err, "danger")
        return redirect(url_for('faculty_marks', academic_year=academic_year, subject_id=subject_id, division=division))

    if not data_dict or not data_dict.get("students"):
        flash("No student records found for the selected filters.", "warning")
        return redirect(url_for('faculty_marks', academic_year=academic_year, subject_id=subject_id, division=division))

    pdf_buffer = generate_internal_marks_pdf(data_dict, faculty_name=faculty_name)

    sub_code = sanitize_filename_component(data_dict["subject"]["subject_code"])
    div_str = sanitize_filename_component(data_dict["division"])
    ay_str = sanitize_filename_component(data_dict["academic_year"])
    filename = f"CampusSync_Internal_Marks_{sub_code}_{div_str}_{ay_str}.pdf"

    return send_file(
        pdf_buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename
    )


@faculty_bp.route('/faculty/students', endpoint='faculty_students')
@faculty_required
def faculty_students():
    """
    Faculty Students Module Route:
    - Allows logged-in faculty to view students belonging to their assigned Subject + Division.
    - Provides top filters: Academic Year, Assigned Subject & Division, Division.
    - Enforces view-only access with roll numbers sorted numerically ascending.
    """
    faculty_id = session.get("faculty_id")
    faculty = get_faculty_model(faculty_id)
    assigned_subjects = get_faculty_assigned_subjects(faculty_id)

    academic = get_academic_settings()
    current_year = academic.academic_year if academic else "2026-27"
    available_years = [current_year]
    selected_year = request.args.get("academic_year", current_year).strip()

    selected_semester = request.args.get("semester")
    if selected_semester:
        try:
            selected_semester = int(selected_semester)
        except ValueError:
            selected_semester = None

    selected_subject_id = request.args.get("subject_id")
    if selected_subject_id:
        try:
            selected_subject_id = int(selected_subject_id)
        except ValueError:
            selected_subject_id = None

    selected_division = request.args.get("division", "").strip().upper()

    # Default to first assigned subject and division if none explicitly selected
    if not selected_subject_id and assigned_subjects:
        selected_subject_id = assigned_subjects[0]["subject_id"]
        selected_division = assigned_subjects[0]["division"]
        selected_semester = assigned_subjects[0]["semester"]
    elif selected_subject_id and not selected_division and assigned_subjects:
        for asub in assigned_subjects:
            if asub["subject_id"] == selected_subject_id:
                selected_division = asub["division"]
                if not selected_semester:
                    selected_semester = asub["semester"]
                break

    student_data = None
    error_msg = None

    if selected_subject_id and selected_division:
        student_data, error_msg = get_faculty_assigned_students(
            faculty_id=faculty_id,
            subject_id=selected_subject_id,
            division=selected_division,
            semester=selected_semester,
            academic_year=selected_year
        )

    return render_template(
        'faculty/students.html',
        faculty=faculty,
        assigned_subjects=assigned_subjects,
        available_years=available_years,
        selected_year=selected_year,
        selected_semester=selected_semester,
        selected_subject_id=selected_subject_id,
        selected_division=selected_division,
        student_data=student_data,
        error=error_msg,
        active_page='students'
    )





# ==============================================================================
# ==============================================================================
# FACULTY QR ATTENDANCE API ENDPOINTS (Fully Exception Handled)
# ==============================================================================
@faculty_bp.route('/faculty/qr/start-session', methods=['POST'], endpoint='faculty_qr_start_session')
@faculty_required
def faculty_qr_start_session():
    """
    Initializes or activates a live QR Attendance session for a specific lecture.
    All exceptions handled cleanly with professional English feedback.
    """
    try:
        faculty_id = session.get("faculty_id")
        data = request.get_json() or {}
        subject_id = data.get("subject_id")
        division = data.get("division")
        semester = data.get("semester")
        academic_year = data.get("academic_year", "2026-27")
        lecture_date = data.get("lecture_date") or datetime.now().strftime("%Y-%m-%d")
        lecture_no = data.get("lecture_no") or "Lecture 1"

        if not subject_id or not division or not semester:
            return jsonify({"success": False, "message": "Missing required lecture details (subject, division, or semester)."}), 400

        try:
            subject_id = int(subject_id)
            semester = int(semester)
        except (ValueError, TypeError):
            return jsonify({"success": False, "message": "Invalid subject ID or semester parameter. Numbers required."}), 400

        session_type = data.get("session_type", "Lecture").strip()
        start_time = data.get("start_time")
        end_time = data.get("end_time")
        if start_time and end_time:
            lecture_no = f"{session_type} ({start_time} - {end_time})"

        from services.attendance_service import start_faculty_qr_session
        res = start_faculty_qr_session(
            faculty_id=faculty_id,
            subject_id=subject_id,
            division=division,
            semester=semester,
            academic_year=academic_year,
            lecture_date=lecture_date,
            lecture_no=lecture_no,
            session_type=session_type,
            start_time=start_time,
            end_time=end_time
        )
        status_code = 200 if res.get("success") else 400
        return jsonify(res), status_code
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error starting QR session: {str(e)}"}), 500


@faculty_bp.route('/faculty/qr/live-status/<int:session_id>', methods=['GET'], endpoint='faculty_qr_live_status')
@faculty_required
def faculty_qr_live_status(session_id):
    """
    Returns real-time scan attendance roster and pending security alerts for faculty dashboard.
    Protected with full exception handling.
    """
    try:
        from services.attendance_service import get_faculty_qr_live_roster
        res = get_faculty_qr_live_roster(session_id)
        status_code = 200 if res.get("success") else 400
        return jsonify(res), status_code
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error retrieving live roster: {str(e)}", "roster": [], "pending_alerts": []}), 500


@faculty_bp.route('/faculty/qr/stop-session', methods=['POST'], endpoint='faculty_qr_stop_session')
@faculty_required
def faculty_qr_stop_session():
    """
    Stops/Closes the active QR attendance session.
    """
    try:
        data = request.get_json() or {}
        session_id = data.get("session_id")
        if not session_id:
            return jsonify({"success": False, "message": "Missing required session ID parameter."}), 400

        try:
            session_id = int(session_id)
        except (ValueError, TypeError):
            return jsonify({"success": False, "message": "Invalid session ID format."}), 400

        from services.attendance_service import stop_faculty_qr_session
        success, msg = stop_faculty_qr_session(session_id)
        status_code = 200 if success else 400
        return jsonify({"success": success, "message": msg}), status_code
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error stopping QR session: {str(e)}"}), 500


@faculty_bp.route('/faculty/qr/resolve-alert', methods=['POST'], endpoint='faculty_qr_resolve_alert')
@faculty_required
def faculty_qr_resolve_alert():
    """
    Faculty approves or rejects an anti-proxy security conflict.
    """
    try:
        faculty_id = session.get("faculty_id")
        data = request.get_json() or {}
        alert_id = data.get("alert_id")
        action = data.get("action", "REJECT")
        if not alert_id:
            return jsonify({"success": False, "message": "Missing required security alert ID."}), 400

        try:
            alert_id = int(alert_id)
        except (ValueError, TypeError):
            return jsonify({"success": False, "message": "Invalid alert ID format."}), 400

        from services.attendance_service import resolve_faculty_security_alert
        success, msg = resolve_faculty_security_alert(alert_id, faculty_id, action)
        status_code = 200 if success else 400
        return jsonify({"success": success, "message": msg}), status_code
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error resolving security alert: {str(e)}"}), 500


@faculty_bp.route('/faculty/qr/extend-session', methods=['POST'], endpoint='faculty_qr_extend_session')
@faculty_required
def faculty_qr_extend_session():
    """
    Extends active/expired QR session by requested minutes (default 2 minutes).
    """
    try:
        data = request.get_json() or {}
        session_id = data.get("session_id")
        if not session_id:
            return jsonify({"success": False, "message": "Missing required session ID parameter."}), 400

        try:
            session_id = int(session_id)
        except (ValueError, TypeError):
            return jsonify({"success": False, "message": "Invalid session ID format."}), 400

        try:
            minutes = int(data.get("minutes", 2))
        except (ValueError, TypeError):
            minutes = 2

        from services.attendance_service import extend_faculty_qr_session
        success, res = extend_faculty_qr_session(session_id, minutes)
        if success:
            return jsonify({"success": True, **res}), 200
        return jsonify({"success": False, "message": res}), 400
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error extending QR session: {str(e)}"}), 500


# ==============================================================================
# Faculty Notice & Assignment Management Routes
# ==============================================================================

@faculty_bp.route('/faculty/notices', methods=['GET'], endpoint='faculty_notices')
@faculty_required
def faculty_notices():
    """Renders the Faculty Notice & Assignment Management console."""
    from services.notification_service import get_faculty_notices, get_faculty_created_notices, mark_all_notifications_as_read
    from services.faculty_service import get_faculty_assigned_subjects
    from services.academic_service import get_academic_settings, get_active_semesters

    faculty_id = session.get('faculty_id')
    if faculty_id:
        try:
            mark_all_notifications_as_read('Faculty', faculty_id)
        except Exception:
            from extensions import db
            db.session.rollback()

    cycle = 'Odd'
    active_semesters = [1, 3, 5]
    try:
        academic = get_academic_settings()
        cycle = academic.semester_cycle if academic else 'Odd'
        active_semesters = get_active_semesters(cycle)
    except Exception as acad_err:
        from extensions import db
        db.session.rollback()
        current_app.logger.warning(f"[Faculty Notices Academic Error] {acad_err}")

    assigned_subjects = []
    try:
        assigned_subjects = get_faculty_assigned_subjects(faculty_id, filter_by_cycle=True)
    except Exception as sub_err:
        from extensions import db
        db.session.rollback()
        current_app.logger.error(f"[Faculty Notices Subject Error] {sub_err}")

    created_notices = []
    try:
        created_notices = get_faculty_created_notices(faculty_id, filter_by_cycle=True)
    except Exception as cn_err:
        from extensions import db
        db.session.rollback()
        current_app.logger.error(f"[Faculty Notices Created Error] {cn_err}")

    campus_notices = []
    try:
        campus_notices = get_faculty_notices(filter_by_cycle=True)
    except Exception as camp_err:
        from extensions import db
        db.session.rollback()
        current_app.logger.error(f"[Faculty Notices Campus Error] {camp_err}")

    from services.notification_service import get_ist_now
    now_ist = get_ist_now()
    expired_with_files = [n for n in created_notices if n.photo_file and n.end_date and n.end_date <= now_ist]

    return render_template(
        'faculty/notices.html',
        assigned_subjects=assigned_subjects,
        created_notices=created_notices,
        campus_notices=campus_notices,
        expired_with_files=expired_with_files,
        now_ist=now_ist,
        active_semesters=active_semesters,
        semester_cycle=cycle,
        active_page='notices'
    )


@faculty_bp.route('/faculty/notices/create', methods=['POST'], endpoint='faculty_create_notice')
@faculty_required
def faculty_create_notice():
    """Handles creating a new class/subject notice or assignment by Faculty."""
    from services.notification_service import create_notification, save_notification_file

    faculty_id = session.get('faculty_id')
    title = request.form.get('title', '').strip()
    message = request.form.get('message', '').strip()
    category = request.form.get('category', 'Subject Related').strip()
    priority = request.form.get('priority', 'Normal').strip()
    subject_id = request.form.get('subject_id')
    semester = request.form.get('semester')
    division = request.form.get('division', 'All').strip()
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')

    if not title or not message:
        flash('Notice title and description instructions are required.', 'danger')
        return redirect(url_for('faculty_notices'))

    # If assignment category, validate that deadline is given
    if category == 'Assignment Submit Date' and not end_date:
        flash('Submission Deadline (End Date & Time) is required for Assignments.', 'warning')
        return redirect(url_for('faculty_notices'))

    # Handle optional photo or question paper / PDF upload
    try:
        file = request.files.get('attachment')
        photo_file, file_type = save_notification_file(file)

        notif = create_notification(
            title=title,
            message=message,
            category=category,
            posted_by_role='Faculty',
            faculty_id=faculty_id,
            target_audience='Student',
            target_semester=semester,
            target_division=division,
            subject_id=subject_id,
            start_date=start_date,
            end_date=end_date,
            priority=priority,
            photo_file=photo_file,
            file_type=file_type
        )

        if notif:
            flash(f"Notice/Assignment '{title}' posted successfully for students!", 'success')
        else:
            flash("Failed to publish notification due to a server error. Please try again.", 'danger')
    except Exception as e:
        from extensions import db
        db.session.rollback()
        current_app.logger.error(f"[faculty_create_notice Error] {e}", exc_info=True)
        flash(f"Error publishing notice: {str(e)}", 'danger')

    return redirect(url_for('faculty_notices'))


@faculty_bp.route('/faculty/notices/delete/<int:notice_id>', methods=['POST'], endpoint='faculty_delete_notice')
@faculty_required
def faculty_delete_notice(notice_id):
    """Deletes a notice created by the logged-in faculty."""
    from services.notification_service import delete_notification
    faculty_id = session.get('faculty_id')
    success, msg = delete_notification(notice_id, 'Faculty', faculty_id)
    if success:
        flash(msg, 'success')
    else:
        flash(msg, 'danger')
    return redirect(url_for('faculty_notices'))


@faculty_bp.route('/faculty/assignments', endpoint='faculty_assignments')
@faculty_required
def faculty_assignments():
    """
    Renders the Faculty Assignment Submissions & Verification Dashboard:
    - Lists all assignment notices posted by this faculty (scoped to active cycle).
    - Allows faculty to select an assignment and check/tick students who submitted.
    - Displays deadline status, submission percentages, and auto-sync triggers.
    """
    faculty_id = session.get('faculty_id')
    from services.assignment_service import (
        get_faculty_assignment_notices,
        get_assignment_submission_sheet
    )

    assignment_notices = get_faculty_assignment_notices(faculty_id, filter_by_cycle=True)
    valid_ids = [item['notice'].id for item in assignment_notices]

    selected_assignment_id = request.args.get('assignment_id', type=int)
    if selected_assignment_id and selected_assignment_id not in valid_ids:
        selected_assignment_id = None

    # If no assignment_id passed but notices exist, pick the first one by default
    if not selected_assignment_id and assignment_notices:
        selected_assignment_id = assignment_notices[0]['notice'].id

    sheet_data = None
    if selected_assignment_id:
        sheet_data = get_assignment_submission_sheet(selected_assignment_id, faculty_id=faculty_id)

    return render_template(
        'faculty/assignments.html',
        assignment_notices=assignment_notices,
        selected_assignment_id=selected_assignment_id,
        sheet_data=sheet_data,
        active_page='assignments'
    )


@faculty_bp.route('/faculty/assignments/save', methods=['POST'], endpoint='faculty_save_assignment_submissions')
@faculty_required
def faculty_save_assignment_submissions():
    """
    Handles bulk submission save and automatic internal marks transfer:
    - Updates AssignmentSubmission records (is_submitted, submitted_at, marks_awarded).
    - Syncs to Internal Assessment Marks (home_assignment / journal) with full or custom marks.
    - Recalculates student's internal assessment total.
    """
    faculty_id = session.get('faculty_id')
    notification_id = request.form.get('notification_id', type=int)
    if not notification_id:
        flash("Invalid assignment selected.", "danger")
        return redirect(url_for('faculty_assignments'))

    submitted_students = request.form.getlist('submitted_students')
    sync_internal = request.form.get('sync_internal', '1') == '1'

    # Extract custom marks if provided by faculty
    custom_marks = {}
    for key, val in request.form.items():
        if key.startswith('marks_') and val.strip():
            sid = key.split('marks_')[1]
            try:
                custom_marks[sid] = float(val)
            except (ValueError, TypeError):
                pass

    from services.assignment_service import save_assignment_submissions
    success, message = save_assignment_submissions(
        notification_id=notification_id,
        faculty_id=faculty_id,
        submitted_student_ids=submitted_students,
        custom_marks_dict=custom_marks,
        sync_to_internal_marks=sync_internal
    )

    if success:
        flash(message, "success")
    else:
        flash(message, "danger")

    return redirect(url_for('faculty_assignments', assignment_id=notification_id))
