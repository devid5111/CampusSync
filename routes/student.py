"""
CampusSync ERP - Student Routes
===============================
File: routes/student.py

Handles Student Authentication (Login / Logout) and Student Dashboard.
"""

from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from services.student_service import authenticate_student, get_student_by_id
from services.attendance_service import get_student_attendance_dashboard, mark_student_qr_attendance, generate_student_attendance_pdf_bytes

student_bp = Blueprint('student', __name__)


# --- Student Context Processor ---
@student_bp.context_processor
def inject_current_student():
    """Injects current logged-in Student object into all student templates."""
    if "student_id" in session:
        from flask import g
        if not hasattr(g, 'current_student'):
            student = get_student_by_id(session["student_id"])
            if not student or getattr(student, 'status', None) != 'Active':
                session.pop("student_id", None)
                g.current_student = None
            else:
                g.current_student = student
        return dict(current_student=g.current_student)
    return dict(current_student=None)


# --- Student Login Route ---
@student_bp.route('/student/login', methods=['GET', 'POST'], endpoint='student_login')
@student_bp.route('/auth/student-login', methods=['GET', 'POST'])
def student_login():
    """
    Handles Student Login logic:
    - GET: Displays student login page.
    - POST: Verifies student email and password, creates session upon success.
    - Prevents device duplication (1 Phone = 1 Student).
    """
    # Check if student is already logged in (GET requests only)
    if request.method == 'GET' and "student_id" in session:
        return redirect(url_for('student_dashboard'))

    if request.method == 'POST':
        payload = request.get_json(silent=True) or request.form.to_dict() or {}
        email = payload.get('email', '').strip()
        password = payload.get('password', '')
        device_fp = payload.get('device_fingerprint', '').strip()
        device_model = payload.get('device_model', '').strip()
        skip_binding_confirmed = str(payload.get('skip_binding_confirmed', '0')).strip()

        print(f"[Student Login] Attempt for: {email} | Device FP: '{device_fp}' | SkipConfirmed: {skip_binding_confirmed}")

        from models import Student, db

        # 1. Check if confirming device conflict modal ("Continue Login")
        if skip_binding_confirmed == '1':
            student_id = session.pop("pending_conflict_student_id", None) or payload.get("student_id")
            student = Student.query.get(student_id) if student_id else None
            if not student and email:
                student = Student.query.filter_by(email=email.lower()).first()
            if not student:
                if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({"status": "error", "message": "Session expired. Please log in again."}), 400
                return redirect(url_for('student_login'))

            # Verified student: DO NOT bind this foreign phone! Keep device_fingerprint as NULL
            session.permanent = True
            session["student_id"] = student.id
            session["user_role"] = "student"
            session["device_conflict_notice"] = "Logged in from shared device. Device not bound."
            session["last_active"] = datetime.utcnow().timestamp()

            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"status": "success", "redirect_url": url_for('student_dashboard')})
            return redirect(url_for('student_dashboard'))

        # 2. Authenticate student credentials
        student, error = authenticate_student(email, password)
        if error:
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"status": "error", "message": error}), 401
            return render_template('auth/student-login.html', error=error)

        # 3. Smart Device Binding & Duplication Prevention Check
        if device_fp:
            # Check if this phone is ALREADY registered to another student
            other_student = Student.query.filter(
                Student.id != student.id,
                db.or_(
                    Student.device_fingerprint == device_fp,
                    Student.device_fingerprint.like(f"{device_fp}:::%"),
                    Student.device_fingerprint.like(f"%:::{device_fp}")
                )
            ).first()

            if other_student:
                # Device belongs to someone else!
                session["pending_conflict_student_id"] = student.id
                conflict_data = {
                    "student_id": student.id,
                    "email": email,
                    "bound_to_roll": other_student.roll_number,
                    "bound_to_name": other_student.full_name,
                    "device_fingerprint": device_fp,
                    "device_model": device_model
                }
                if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify({
                        "status": "conflict",
                        "conflict": conflict_data,
                        "message": f"This device is already registered to Roll #{other_student.roll_number} ({other_student.full_name})."
                    })
                return render_template('auth/student-login.html', device_conflict=conflict_data)
            else:
                # Device is completely unassigned to anyone -> Bind cleanly on first login!
                if not student.device_fingerprint:
                    student.device_fingerprint = device_fp
                    student.device_model = device_model or "Mobile Device"
                    student.device_bound_at = datetime.utcnow()
                    db.session.commit()

        # 4. Standard clean login
        session.permanent = True
        session["student_id"] = student.id
        session["user_role"] = "student"
        session["last_active"] = datetime.utcnow().timestamp()

        # Check if student already has a registered WebAuthn passkey
        fp = student.device_fingerprint or ''
        if ':::' in fp:
            hw_part, cred_part = fp.split(':::', 1)
            has_passkey = bool(cred_part)
        else:
            has_passkey = bool(fp and not fp.startswith('HW-') and not fp.startswith('DEV-') and not fp.startswith('PIN-'))
        if getattr(student, 'device_reset_allowed', 0):
            has_passkey = False

        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "status": "success",
                "has_passkey": has_passkey,
                "student_id": student.id,
                "email": student.email,
                "full_name": student.full_name,
                "roll_number": student.roll_number,
                "redirect_url": url_for('student_dashboard')
            })
        return redirect(url_for('student_dashboard'))

    success_msg = request.args.get('success')
    return render_template('auth/student-login.html', success=success_msg)


# --- Student Forgot Password Route ---
@student_bp.route('/student/forgot-password', methods=['GET', 'POST'], endpoint='student_forgot_password')
def student_forgot_password():
    """
    Handles Student Password Reset OTP request:
    - GET: Shows forgot password email form.
    - POST: Generates hashed OTP in DB and sends OTP email.
    """
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        from services.password_reset_service import request_password_reset_otp
        success, msg, target_email = request_password_reset_otp('student', email)

        if success:
            return redirect(url_for('student_reset_password', email=target_email))
        else:
            return render_template(
                'auth/forgot-password.html',
                portal_name='Student',
                action_url=url_for('student_forgot_password'),
                login_url=url_for('student_login'),
                email=email,
                error=msg
            )

    preset_email = request.args.get('email', '')
    return render_template(
        'auth/forgot-password.html',
        portal_name='Student',
        action_url=url_for('student_forgot_password'),
        login_url=url_for('student_login'),
        email=preset_email
    )


# --- Student Reset Password Route ---
@student_bp.route('/student/reset-password', methods=['GET', 'POST'], endpoint='student_reset_password')
def student_reset_password():
    """
    Handles Student OTP verification and password update:
    - GET: Shows reset password form.
    - POST: Verifies hashed OTP from DB, updates password, redirects to login.
    """
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        otp = request.form.get('otp', '').strip()
        new_pwd = request.form.get('new_password', '')
        confirm_pwd = request.form.get('confirm_password', '')

        from services.password_reset_service import verify_otp_and_reset_password
        success, msg = verify_otp_and_reset_password('student', email, otp, new_pwd, confirm_pwd)

        if success:
            return redirect(url_for('student_login', success=msg))
        else:
            return render_template(
                'auth/reset-password.html',
                portal_name='Student',
                action_url=url_for('student_reset_password'),
                resend_url=url_for('student_forgot_password', email=email),
                login_url=url_for('student_login'),
                email=email,
                error=msg
            )

    email = request.args.get('email', '')
    return render_template(
        'auth/reset-password.html',
        portal_name='Student',
        action_url=url_for('student_reset_password'),
        resend_url=url_for('student_forgot_password', email=email),
        login_url=url_for('student_login'),
        email=email
    )


# --- Student Dashboard Route ---
@student_bp.route('/student/dashboard', endpoint='student_dashboard')
def student_dashboard():
    """
    Protected Student Dashboard route:
    - Verifies student session ("student_id" in session).
    - Fetches latest student profile data from database using session ID.
    """
    # Check student session
    if "student_id" not in session:
        return redirect(url_for('student_login'))

    # Fetch student details from request context or database
    from flask import g
    student = getattr(g, 'current_student', None) or get_student_by_id(session["student_id"])

    # If student record not found, clear session and redirect to login
    if not student:
        session.pop("student_id", None)
        return redirect(url_for('student_login'))

    # Render Student Dashboard with student details, academic settings, and urgent assignment deadline alerts
    try:
        from services.academic_service import get_academic_settings
        academic = get_academic_settings()
    except Exception as acad_err:
        from flask import current_app
        current_app.logger.warning(f"[Student Dashboard Academic Settings Error] {acad_err}")
        academic = None

    urgent_notices = []
    latest_notices = []
    try:
        from services.notification_service import get_student_urgent_notices, get_student_notices, calculate_deadline_info
        urgent_notices = get_student_urgent_notices(student) or []
        latest_notices = (get_student_notices(student) or [])[:4]
        for n in latest_notices:
            n.deadline_info = calculate_deadline_info(n)
    except Exception as notif_err:
        from flask import current_app
        current_app.logger.error(f"[Student Dashboard Notice Error] {notif_err}")
        urgent_notices = []
        latest_notices = []

    # Safeguard all notice properties against None so Jinja template expressions never fail
    for n in (urgent_notices + latest_notices):
        if not getattr(n, 'title', None):
            n.title = 'Campus Notice'
        if not getattr(n, 'message', None):
            n.message = ''
        if not getattr(n, 'category', None):
            n.category = 'General'

    try:
        return render_template(
            'student/dashboard.html',
            student=student,
            academic=academic,
            urgent_notices=urgent_notices,
            latest_notices=latest_notices,
            active_page='dashboard'
        )
    except Exception as render_err:
        from flask import current_app
        current_app.logger.error(f"[Student Dashboard Render Error] {render_err}", exc_info=True)
        # Fail-safe fallback rendering with empty notices
        return render_template(
            'student/dashboard.html',
            student=student,
            academic=academic,
            urgent_notices=[],
            latest_notices=[],
            active_page='dashboard'
        )


# --- Student Logout Route ---
@student_bp.route('/student/logout', endpoint='student_logout')
def student_logout():
    """
    Handles Student Logout:
    - Clears student session keys.
    - Redirects back to Student Login page.
    """
    session.pop("student_id", None)
    if session.get("user_role") == "student":
        session.pop("user_role", None)
    session.pop("device_conflict_notice", None)
    return redirect(url_for('student_login'))


# --- Student Result & Marks Route ---
@student_bp.route('/student/results', endpoint='student_results')
def student_results():
    """
    Student Result Portal:
    - Filters marks by Academic Year and Semester.
    - Displays detailed card-based results per subject with component breakdowns.
    - Provides a placeholder Download Marksheet feature.
    """
    if "student_id" not in session:
        return redirect(url_for('student_login'))

    student = get_student_by_id(session["student_id"])
    if not student:
        session.pop("student_id", None)
        return redirect(url_for('student_login'))

    import json
    from models import Subject, InternalMark

    # Current student semester
    st_sem = student.semester or 1

    # Filter parameters: default to latest completed semester (or 1 if in Sem 1)
    default_sem = (st_sem - 1) if st_sem > 1 else 1
    req_sem = request.args.get('semester')
    try:
        selected_sem = int(req_sem) if req_sem is not None else default_sem
    except (ValueError, TypeError):
        selected_sem = default_sem

    # Query marks for this student
    marks_records = InternalMark.query.filter_by(
        student_id=student.id,
        semester=selected_sem
    ).all()
    marks_by_subject = {m.subject_id: m for m in marks_records}

    # Detect the actual academic year for the selected semester
    if marks_records and marks_records[0].academic_year:
        selected_year = marks_records[0].academic_year
    else:
        selected_year = request.args.get('academic_year', '2026-27').strip()

    # Fetch subjects for the selected semester
    subjects = Subject.query.filter_by(semester=selected_sem).all()

    # Also query across all semesters to know which semesters have marks
    all_student_marks = InternalMark.query.filter_by(student_id=student.id).all()
    semesters_with_marks = sorted(list(set(m.semester for m in all_student_marks if m.semester)))

    results_data = []
    total_obtained = 0.0
    total_max = 0.0
    evaluated_count = 0

    for sub in subjects:
        m = marks_by_subject.get(sub.id)
        max_marks = sub.internal_marks or (50 if sub.subject_type == 'Theory' else 25)
        breakdown = {}
        marks_obtained = None
        percentage = None

        if m:
            marks_obtained = m.marks_obtained
            if marks_obtained is not None:
                total_obtained += marks_obtained
                evaluated_count += 1
            total_max += max_marks

            from services.subject_service import get_subject_component_config
            comp_cfg = get_subject_component_config(sub)
            sub_maxes = comp_cfg.get("maxes", {})
            sub.maxes = sub_maxes
            sub.components = comp_cfg.get("components", [])

            if m.component_data:
                try:
                    breakdown = json.loads(m.component_data)
                except Exception:
                    breakdown = {}
            if not breakdown:
                breakdown = {
                    "type": sub.subject_type,
                    "test1": m.test1, "test2": m.test2, "test3": m.test3,
                    "quiz1": m.quiz1, "quiz2": m.quiz2, "quiz3": m.quiz3, "quiz4": m.quiz4,
                    "active_learning": m.active_learning, "class_assignment": m.class_assignment,
                    "home_assignment": m.home_assignment, "attendance": m.attendance,
                    "internal_exam": m.internal_exam, "practical_eval": m.practical_eval, "viva": m.viva, "journal": m.journal,
                    "total": m.marks_obtained
                }

            # Ensure dynamic maxes and best2 calculation are always available
            breakdown['maxes'] = sub_maxes
            if breakdown.get('best2') is None:
                t_vals = [float(t) for t in [breakdown.get('test1'), breakdown.get('test2'), breakdown.get('test3')] if t is not None]
                if t_vals:
                    t_vals.sort(reverse=True)
                    best2_vals = t_vals[:2]
                    breakdown['best2'] = round(sum(best2_vals) / len(best2_vals), 2)
            if marks_obtained is not None and max_marks > 0:
                percentage = round((marks_obtained / max_marks) * 100, 1)
        else:
            total_max += max_marks

        results_data.append({
            "subject": sub,
            "mark_record": m,
            "marks_obtained": marks_obtained,
            "max_marks": max_marks,
            "percentage": percentage,
            "breakdown": breakdown,
            "status": "Evaluated" if (m and marks_obtained is not None) else "Pending"
        })

    overall_percentage = round((total_obtained / total_max) * 100, 1) if (total_max > 0 and total_obtained > 0) else 0.0

    available_years = ['2026-27', '2025-26', '2024-25']
    # Active students can only navigate up to their current enrolled semester
    available_semesters = list(range(1, st_sem + 1))

    from services.result_declaration_service import is_result_declared
    from services.college_service import get_college_settings

    # Ongoing semester (selected_sem >= st_sem) results are not declared yet
    if selected_sem >= st_sem:
        is_declared = False
    else:
        is_declared = is_result_declared(selected_year, selected_sem, student.division)
        if not is_declared and len(marks_records) > 0:
            is_declared = True

    college = get_college_settings()

    return render_template(
        'student/results.html',
        student=student,
        college=college,
        active_page='results',
        selected_year=selected_year,
        selected_sem=selected_sem,
        available_years=available_years,
        available_semesters=available_semesters,
        semesters_with_marks=semesters_with_marks,
        results_data=results_data,
        total_obtained=round(total_obtained, 2),
        total_max=round(total_max, 2),
        evaluated_count=evaluated_count,
        total_subjects=len(subjects),
        overall_percentage=overall_percentage,
        is_result_declared=is_declared
    )


@student_bp.route('/student/results/pdf', endpoint='student_download_result_pdf')
def student_download_result_pdf():
    """Generates official single-student Internal Assessment Result PDF for logged-in student."""
    if "student_id" not in session:
        return redirect(url_for('student_login'))

    student = get_student_by_id(session["student_id"])
    if not student:
        session.pop("student_id", None)
        return redirect(url_for('student_login'))

    try:
        st_sem = student.semester or 1
        default_sem = (st_sem - 1) if st_sem > 1 else 1
        try:
            semester = int(request.args.get('semester', default_sem))
        except (ValueError, TypeError):
            semester = default_sem

        # Security check: Ongoing semester cannot be downloaded
        if semester >= st_sem:
            flash(f"Internal Assessment Result for Semester {semester} has not been declared yet.", "warning")
            return redirect(url_for('student.student_results', semester=semester))

        from models import InternalMark
        mark_sample = InternalMark.query.filter_by(student_id=student.id, semester=semester).first()
        academic_year = mark_sample.academic_year if (mark_sample and mark_sample.academic_year) else request.args.get('academic_year', '2026-27').strip()

        from services.result_declaration_service import is_result_declared
        if not is_result_declared(academic_year, semester, student.division) and not mark_sample:
            flash(f"Internal Assessment Result for Semester {semester} ({academic_year}) has not been declared by administration yet.", "warning")
            return redirect(url_for('student.student_results', semester=semester, academic_year=academic_year))

        from services.admin_results_service import get_admin_student_full_result
        from services.export_marks_service import generate_student_result_pdf, sanitize_filename_component
        from flask import send_file

        result_data, err = get_admin_student_full_result(
            student_id=student.id,
            semester=semester,
            academic_year=academic_year
        )
        if err or not result_data:
            flash(f"Marksheet generation error: {err or 'No data found'}", "danger")
            return redirect(url_for('student.student_results', semester=semester, academic_year=academic_year))

        buffer = generate_student_result_pdf(result_data)
        st_enroll = sanitize_filename_component(student.enrollment_no or f"Roll_{student.roll_number}")
        filename = f"Internal_Result_Sem_{semester}_{st_enroll}.pdf"

        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype="application/pdf"
        )
    except Exception as e:
        flash(f"Failed to generate marksheet PDF: {str(e)}", "danger")
        return redirect(url_for('student.student_results'))


# --- Placeholder Student Routes (Prevents sidebar url_for BuildError) ---
@student_bp.route('/student/attendance', endpoint='student_attendance')
def student_attendance():
    """
    Student Attendance Dashboard Route:
    Displays Overall Circular Attendance Gauge, Subject Breakdown, and Daily Lecture Log.
    """
    if "student_id" not in session:
        return redirect(url_for('student_login'))

    student = get_student_by_id(session["student_id"])
    if not student:
        session.pop("student_id", None)
        return redirect(url_for('student_login'))

    att_data = get_student_attendance_dashboard(student.id)

    return render_template(
        'student/attendance.html',
        student=student,
        att_data=att_data,
        active_page='attendance'
    )


@student_bp.route('/student/attendance/pdf', endpoint='student_attendance_pdf')
def student_attendance_pdf():
    """
    Downloads official PDF attendance summary report for logged-in student.
    """
    if "student_id" not in session:
        return redirect(url_for('student_login'))

    student = get_student_by_id(session["student_id"])
    if not student:
        session.pop("student_id", None)
        return redirect(url_for('student_login'))

    pdf_buffer = generate_student_attendance_pdf_bytes(student.id)
    if not pdf_buffer:
        flash("Failed to generate PDF report.", "danger")
        return redirect(url_for('student_attendance'))

    return send_file(
        pdf_buffer,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"Attendance_Report_{student.enrollment_no}.pdf"
    )


@student_bp.route('/student/scan', methods=['GET', 'POST'], endpoint='student_scan')
@student_bp.route('/student/scan-qr', methods=['GET', 'POST'], endpoint='student_scan_qr')
def student_scan():
    """
    Student QR Code Scan Route:
    - GET: Checks for active lecture QR session for student's Semester & Division.
    - POST: Validates QR Token, Campus GPS Geofence, and Device Binding (1 Phone = 1 Student).
    All exceptions handled cleanly with professional English feedback.
    """
    if "student_id" not in session:
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": "Session expired. Please log in again."}), 401
        return redirect(url_for('student_login'))

    student = get_student_by_id(session["student_id"])
    if not student:
        session.pop("student_id", None)
        return redirect(url_for('student_login'))

    if request.method == 'POST':
        try:
            payload = request.get_json() or request.form.to_dict() or {}
            session_id = payload.get("session_id")
            if session_id:
                try:
                    session_id = int(session_id)
                except (ValueError, TypeError):
                    session_id = None

            token = payload.get("token")
            lat = payload.get("latitude") or payload.get("lat")
            lng = payload.get("longitude") or payload.get("lng")
            try:
                lat = float(lat) if lat is not None else None
            except (ValueError, TypeError):
                lat = None
            try:
                lng = float(lng) if lng is not None else None
            except (ValueError, TypeError):
                lng = None

            device_fingerprint = payload.get("device_fingerprint")
            device_model = payload.get("device_model")
            request_approval = bool(payload.get("request_approval") or payload.get("is_borrowed_device"))
            alert_id = payload.get("alert_id")
            if alert_id:
                try:
                    alert_id = int(alert_id)
                except (ValueError, TypeError):
                    alert_id = None

            from services.attendance_service import mark_student_qr_attendance
            success, msg_or_data = mark_student_qr_attendance(
                student_id=student.id,
                session_id=session_id,
                token=token,
                lat=lat,
                lng=lng,
                device_fingerprint=device_fingerprint,
                device_model=device_model,
                request_approval=request_approval,
                alert_id=alert_id
            )

            if isinstance(msg_or_data, dict):
                response_payload = {"success": success, **msg_or_data}
                user_msg = msg_or_data.get("message", "Attendance request processed.")
            else:
                response_payload = {"success": success, "message": str(msg_or_data)}
                user_msg = str(msg_or_data)

            status_code = 200 if (success or response_payload.get("requires_approval") or response_payload.get("pending_approval")) else 400
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(response_payload), status_code

            if success:
                flash(user_msg, "success")
            else:
                flash(user_msg, "danger")

            return redirect(url_for('student_attendance'))
        except Exception as e:
            err_msg = f"An unexpected error occurred during attendance verification: {str(e)}"
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"success": False, "message": err_msg}), 500
            flash(err_msg, "danger")
            return redirect(url_for('student_scan'))

    # GET Request: check if an active QR session exists for student's semester & division
    try:
        from models import LectureAttendanceSession, LectureAttendanceStudent
        active_session = LectureAttendanceSession.query.filter_by(
            semester=student.semester,
            division=student.division,
            is_qr_active=1
        ).order_by(LectureAttendanceSession.id.desc()).first()

        already_marked = False
        marked_at_str = None
        scan_status = 'ready'  # 'ready', 'verified', 'rejected', 'pending'
        security_notice = None

        if active_session:
            from models import AttendanceSecurityAlert
            security_alert = AttendanceSecurityAlert.query.filter_by(
                session_id=active_session.id,
                student_id=student.id
            ).order_by(AttendanceSecurityAlert.id.desc()).first()

            existing_rejected = LectureAttendanceStudent.query.filter_by(
                session_id=active_session.id,
                student_id=student.id,
                status='Absent'
            ).first()

            if security_alert and security_alert.faculty_action == 'REJECTED':
                scan_status = 'rejected'
                security_notice = security_alert.alert_message or "Proxy scan rejected by faculty. You are marked Absent."
            elif existing_rejected and existing_rejected.marked_method == 'REJECTED_PROXY':
                scan_status = 'rejected'
                security_notice = "Proxy scan rejected by faculty. You are marked Absent."
            elif security_alert and security_alert.faculty_action == 'PENDING':
                scan_status = 'pending'
                security_notice = "Your attendance scan has been flagged for proxy review and is awaiting faculty decision."
            else:
                existing_rec = LectureAttendanceStudent.query.filter_by(
                    session_id=active_session.id,
                    student_id=student.id,
                    status='Present'
                ).first()
                if existing_rec:
                    already_marked = True
                    scan_status = 'verified'
                    if existing_rec.scanned_at:
                        marked_at_str = existing_rec.scanned_at.strftime("%I:%M:%S %p")

        return render_template(
            'student/scan.html',
            student=student,
            active_session=active_session,
            already_marked=already_marked,
            marked_at_str=marked_at_str,
            scan_status=scan_status,
            security_notice=security_notice,
            active_page='scan'
        )
    except Exception as e:
        flash(f"Error loading attendance scan portal: {str(e)}", "danger")
        return redirect(url_for('student_dashboard'))


@student_bp.route('/student/scan/check-status', methods=['GET'], endpoint='student_scan_check_status')
def student_scan_check_status():
    """
    Lightweight polling endpoint returning the real-time review status of an attendance scan.
    Enables automatic transitions from pending to verified or rejected.
    """
    if "student_id" not in session:
        return jsonify({"success": False, "status": "unauthorized"}), 401

    try:
        student_id = session.get("student_id")
        student = get_student_by_id(student_id)
        if not student:
            return jsonify({"success": False, "status": "unknown"}), 404

        session_id = request.args.get("session_id")
        from models import LectureAttendanceSession, LectureAttendanceStudent, AttendanceSecurityAlert

        sess = None
        if session_id:
            try:
                sess = LectureAttendanceSession.query.get(int(session_id))
            except (ValueError, TypeError):
                pass

        if not sess:
            sess = LectureAttendanceSession.query.filter_by(
                semester=student.semester,
                division=student.division,
                is_qr_active=1
            ).order_by(LectureAttendanceSession.id.desc()).first()

        if not sess:
            return jsonify({"success": True, "status": "no_session"})

        security_alert = AttendanceSecurityAlert.query.filter_by(
            session_id=sess.id,
            student_id=student.id
        ).order_by(AttendanceSecurityAlert.id.desc()).first()

        existing_student_rec = LectureAttendanceStudent.query.filter_by(
            session_id=sess.id,
            student_id=student.id
        ).first()

        if security_alert and security_alert.faculty_action == 'APPROVED':
            marked_time = existing_student_rec.scanned_at.strftime("%I:%M:%S %p") if (existing_student_rec and existing_student_rec.scanned_at) else "Just Now"
            return jsonify({
                "success": True,
                "status": "verified",
                "marked_at": marked_time,
                "message": "Your attendance request has been approved by the faculty! You are marked PRESENT."
            })
        elif security_alert and security_alert.faculty_action == 'REJECTED':
            return jsonify({
                "success": True,
                "status": "rejected",
                "message": security_alert.alert_message or "Attendance request was rejected by the faculty. Marked ABSENT."
            })
        elif existing_student_rec and existing_student_rec.status == 'Present':
            marked_time = existing_student_rec.scanned_at.strftime("%I:%M:%S %p") if existing_student_rec.scanned_at else "Just Now"
            return jsonify({
                "success": True,
                "status": "verified",
                "marked_at": marked_time,
                "message": "Attendance verified successfully."
            })
        elif existing_student_rec and existing_student_rec.marked_method == 'REJECTED_PROXY':
            return jsonify({
                "success": True,
                "status": "rejected",
                "message": "Attendance scan flagged and rejected by faculty."
            })
        elif security_alert and security_alert.faculty_action == 'PENDING':
            return jsonify({
                "success": True,
                "status": "pending",
                "message": "Attendance request submitted. Awaiting faculty approval."
            })
        else:
            return jsonify({"success": True, "status": "ready"})
    except Exception as e:
        return jsonify({"success": False, "status": "error", "message": str(e)}), 500


@student_bp.route('/student/scan/cancel-proxy-alert', methods=['POST'], endpoint='student_scan_cancel_proxy_alert')
def student_scan_cancel_proxy_alert():
    """
    Called when a student dismisses/cancels the borrowed device warning modal.
    Updates the security alert message so faculty sees that the proxy attempt was cancelled/aborted.
    """
    if "student_id" not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401
    payload = request.get_json(silent=True) or {}
    alert_id = payload.get("alert_id")
    if alert_id:
        try:
            alert_id = int(alert_id)
            from models import AttendanceSecurityAlert, db
            alert = AttendanceSecurityAlert.query.get(alert_id)
            if alert and alert.student_id == session["student_id"]:
                if "[ATTEMPT CANCELLED BY STUDENT]" not in (alert.alert_message or ""):
                    alert.alert_message = f"{alert.alert_message} [ATTEMPT CANCELLED BY STUDENT ON WARNING MODAL]"
                    db.session.commit()
        except Exception as e:
            from models import db
            db.session.rollback()
    return jsonify({"success": True})


@student_bp.route('/student/notices', endpoint='student_notices')
def student_notices():
    """
    Renders Student Notices & Assignments portal:
    - Filters by selected category (All, Test, Assignment Submit Date, Subject Related, Other).
    - Calculates deadline countdown, remaining hours/mins, and urgent status.
    """
    if "student_id" not in session:
        return redirect(url_for('student_login'))

    student = get_student_by_id(session["student_id"])
    if not student:
        session.pop("student_id", None)
        return redirect(url_for('student_login'))

    selected_category = request.args.get('category', 'All').strip()
    from services.notification_service import get_student_notices, calculate_deadline_info, mark_all_notifications_as_read

    try:
        mark_all_notifications_as_read('Student', student.id)
    except Exception as mark_err:
        from extensions import db
        db.session.rollback()

    notices = []
    try:
        notices = get_student_notices(student, category=selected_category)
        for n in notices:
            try:
                n.deadline_info = calculate_deadline_info(n)
            except Exception:
                n.deadline_info = {'has_deadline': False, 'is_expired': False, 'is_urgent': False}
    except Exception as notif_err:
        from extensions import db
        from flask import current_app
        db.session.rollback()
        current_app.logger.error(f"[Student Notices Error] {notif_err}")
        notices = []

    return render_template(
        'student/notices.html',
        student=student,
        notices=notices,
        selected_category=selected_category,
        active_page='notices'
    )





# --- Student WebAuthn Endpoints ---
@student_bp.route('/student/webauthn/status', methods=['GET'], endpoint='student_webauthn_status')
def student_webauthn_status():
    if "student_id" not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401
    student = get_student_by_id(session["student_id"])
    if not student:
        return jsonify({"success": False, "message": "Student not found"}), 404

    fp = student.device_fingerprint or ''
    if ':::' in fp:
        hw_part, cred_part = fp.split(':::', 1)
        has_passkey = bool(cred_part)
        credential_id = cred_part if has_passkey else None
    else:
        has_passkey = bool(fp and not fp.startswith('HW-') and not fp.startswith('DEV-') and not fp.startswith('PIN-'))
        credential_id = fp if has_passkey else None
    
    reset_allowed = bool(getattr(student, 'device_reset_allowed', 0))
    if reset_allowed:
        has_passkey = False

    return jsonify({
        "success": True,
        "has_passkey": has_passkey,
        "credential_id": credential_id,
        "student_id": student.id,
        "email": student.email,
        "full_name": student.full_name,
        "roll_number": student.roll_number,
        "reset_allowed": reset_allowed
    })


@student_bp.route('/student/webauthn/all-credentials', methods=['GET'], endpoint='student_webauthn_all_credentials')
def student_webauthn_all_credentials():
    from models import Student
    current_sid = session.get("student_id")
    students_with_cred = Student.query.filter(
        Student.device_fingerprint.isnot(None),
        Student.id != current_sid
    ).all()

    creds = []
    for s in students_with_cred:
        if s.device_fingerprint:
            raw = s.device_fingerprint.strip()
            if ':::' in raw:
                raw = raw.split(':::')[1].strip()
            if raw and not raw.startswith('HW-') and not raw.startswith('DEV-') and not raw.startswith('PIN-'):
                creds.append(raw)

    return jsonify({
        "success": True,
        "credentials": creds
    })


@student_bp.route('/student/webauthn/register', methods=['POST'], endpoint='student_webauthn_register')
def student_webauthn_register():
    if "student_id" not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401
    student = get_student_by_id(session["student_id"])
    if not student:
        return jsonify({"success": False, "message": "Student not found"}), 404

    data = request.get_json() or {}
    credential_id = data.get("credential_id", "").strip()
    device_name = data.get("device_name", "").strip()
    hardware_fp = data.get("hardware_fingerprint", "").strip()

    if not credential_id:
        return jsonify({"success": False, "message": "Invalid credential ID."}), 400

    from models import Student, db

    # 1. Duplication Prevention: Check if this phone credential or hardware FP is bound to another student
    filter_clauses = [
        Student.device_fingerprint == credential_id,
        Student.device_fingerprint.like(f"%:::{credential_id}")
    ]
    if hardware_fp:
        filter_clauses.extend([
            Student.device_fingerprint == hardware_fp,
            Student.device_fingerprint.like(f"{hardware_fp}:::%")
        ])

    other = Student.query.filter(
        Student.id != student.id,
        db.or_(*filter_clauses)
    ).first()

    if other:
        return jsonify({
            "success": False,
            "message": f"This physical device is already bound to Roll #{other.roll_number} ({other.full_name}). Institutional policy permits only 1 student per physical phone."
        }), 409

    # 2. Check if student already has a different phone bound without reset permission
    current_fp = student.device_fingerprint or ''
    current_cred = current_fp.split(':::')[1] if ':::' in current_fp else current_fp
    if current_cred and not current_cred.startswith('HW-') and not current_cred.startswith('DEV-'):
        if current_cred != credential_id and not getattr(student, 'device_reset_allowed', 0):
            return jsonify({
                "success": False,
                "message": "Your account is already bound to another phone. Please contact faculty or admin to reset your device."
            }), 403

    # 3. Save student passkey linked with hardware fingerprint
    if hardware_fp:
        student.device_fingerprint = f"{hardware_fp}:::{credential_id}"
    else:
        student.device_fingerprint = credential_id
    student.device_model = device_name or "Mobile Device"
    student.device_bound_at = datetime.utcnow()
    student.device_reset_allowed = 0

    # Auto-dismiss / deactivate all Device Binding notifications for this student
    from models import Notification
    try:
        Notification.query.filter(
            Notification.target_student_id == student.id,
            Notification.category == 'Device Binding'
        ).update({"is_active": False}, synchronize_session=False)
    except Exception:
        pass

    db.session.commit()

    return jsonify({
        "success": True,
        "message": "Phone screen lock / biometric registered successfully."
    })
