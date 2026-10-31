"""
CampusSync ERP - Comprehensive Mobile Application REST API
===========================================================
File: routes/api.py

Provides full JSON REST API endpoints covering all features of CampusSync:
- Student Portal (Dashboard, Subject Attendance, Internal Marks, Notices, QR Scan)
- Faculty Portal (Dashboard, Assigned Subjects, Lecture Attendance, Marks Entry, QR Code, Student Directory, Notices)
- Admin Portal (Dashboard, Student Directory, Faculty Directory, Subjects Directory, Settings, Notice Management)
Zero impact on existing Flask Jinja2 web templates or routes.
"""

from datetime import datetime
from flask import Blueprint, request, jsonify, session
from extensions import db
from models import (
    Student, Faculty, Admin, Subject, InternalMark,
    AttendanceRecord, Notification, LectureAttendanceSession,
    LectureAttendanceStudent, CollegeSetting, AcademicSetting
)

# Import existing domain services
from services.auth_service import authenticate_admin
from services.faculty_service import (
    authenticate_faculty, get_faculty_by_id, get_faculty_dashboard_data,
    get_faculty_assigned_subjects, get_faculty_marks_students
)
from services.student_service import authenticate_student, get_student_by_id, get_all_students_sorted
from services.attendance_service import (
    get_student_attendance_dashboard, save_lecture_session_attendance,
    mark_student_qr_attendance
)
from services.admin_dashboard_service import get_admin_dashboard_data
from services.notification_service import (
    get_public_notices, create_notification, get_all_admin_notices,
    get_user_notifications_feed, mark_notification_as_read, mark_all_notifications_as_read
)
from services.college_service import get_college_settings

api_bp = Blueprint('api_v1', __name__, url_prefix='/api/v1')


# --- CORS Middleware for API Blueprint Only ---
@api_bp.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, Accept'
    return response


@api_bp.route('/<path:dummy>', methods=['OPTIONS'])
@api_bp.route('/', methods=['OPTIONS'])
def options_handler(dummy=''):
    return jsonify({"status": "ok"}), 200


# ------------------------------------------------------------------------------
# 1. Health / Connection Test
# ------------------------------------------------------------------------------
@api_bp.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "status": "healthy",
        "service": "CampusSync Mobile REST API",
        "timestamp": datetime.utcnow().isoformat()
    }), 200


# ------------------------------------------------------------------------------
# 2. Authentication API (Student, Faculty, Admin)
# ------------------------------------------------------------------------------
@api_bp.route('/auth/login', methods=['POST'])
def api_login():
    """
    Centralized JSON Login for Mobile App.
    Accepts: { username, password, role: 'student'|'faculty'|'admin' }
    """
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    role = (data.get('role') or 'student').lower().strip()

    if not username or not password:
        return jsonify({"success": False, "message": "Username/Email and password are required"}), 400

    try:
        if role == 'student':
            student, err = authenticate_student(username, password)
            if not student:
                return jsonify({"success": False, "message": err or "Invalid student credentials"}), 401
            
            user_profile = {
                "id": f"student_{student.id}",
                "raw_id": student.id,
                "name": student.name,
                "email": student.email,
                "username": student.enrollment_no or student.email,
                "role": "student",
                "roll_number": student.roll_number,
                "enrollment_no": student.enrollment_no,
                "course": student.course,
                "semester": student.semester,
                "division": student.division,
                "academic_year": student.academic_year,
                "mobile": student.mobile
            }

        elif role == 'faculty':
            faculty = authenticate_faculty(username, password)
            if not faculty:
                return jsonify({"success": False, "message": "Invalid faculty credentials"}), 401

            user_profile = {
                "id": f"faculty_{faculty.id}",
                "raw_id": faculty.id,
                "name": faculty.name,
                "email": faculty.email,
                "username": faculty.faculty_code or faculty.email,
                "role": "faculty",
                "faculty_code": faculty.faculty_code,
                "designation": faculty.designation,
                "department": faculty.department,
                "mobile": faculty.mobile
            }

        elif role == 'admin':
            admin = authenticate_admin(username, password)
            if not admin:
                return jsonify({"success": False, "message": "Invalid admin credentials"}), 401

            user_profile = {
                "id": f"admin_{admin.id}",
                "raw_id": admin.id,
                "name": admin.full_name or admin.username,
                "email": admin.email,
                "username": admin.username,
                "role": "admin",
                "designation": "System Administrator",
                "department": "Campus Administration"
            }
        else:
            return jsonify({"success": False, "message": f"Unsupported role: {role}"}), 400

        # Generate tokens
        tokens = {
            "accessToken": f"cs_jwt_{role}_{user_profile['raw_id']}_{int(datetime.utcnow().timestamp())}",
            "refreshToken": f"cs_refresh_{role}_{user_profile['raw_id']}_{int(datetime.utcnow().timestamp())}"
        }

        return jsonify({
            "success": True,
            "message": "Login successful",
            "user": user_profile,
            "tokens": tokens
        }), 200

    except Exception as e:
        return jsonify({"success": False, "message": f"Login error: {str(e)}"}), 500


# ------------------------------------------------------------------------------
# 3. Student Endpoints
# ------------------------------------------------------------------------------
@api_bp.route('/student/dashboard', methods=['GET'])
def api_student_dashboard():
    """Returns student dashboard statistics, attendance %, enrolled subjects, notices."""
    student_id = request.args.get('student_id', type=int)
    if not student_id:
        return jsonify({"success": False, "message": "student_id parameter required"}), 400

    try:
        student = get_student_by_id(student_id)
        if not student:
            return jsonify({"success": False, "message": "Student not found"}), 404

        # Fetch attendance summary
        att_dashboard = get_student_attendance_dashboard(student_id)
        attendance_percentage = 0.0
        subject_attendance = []
        attended_lectures = 0
        total_lectures = 0

        if att_dashboard and 'subjects' in att_dashboard:
            subject_attendance = att_dashboard.get('subjects', [])
            attendance_percentage = att_dashboard.get('overall_percentage', 0.0)
            attended_lectures = att_dashboard.get('total_attended', 0)
            total_lectures = att_dashboard.get('total_conducted', 0)

        # Fetch public/student notices
        notices = []
        try:
            raw_notices = get_public_notices(limit=10)
            for n in raw_notices:
                notices.append({
                    "id": n.id,
                    "title": n.title,
                    "message": n.message,
                    "date": n.created_at.strftime('%d %b %Y') if getattr(n, 'created_at', None) else '',
                    "file_path": getattr(n, 'file_path', None)
                })
        except Exception:
            pass

        return jsonify({
            "success": True,
            "student": {
                "id": student.id,
                "name": student.name,
                "roll_number": student.roll_number,
                "enrollment_no": student.enrollment_no,
                "course": student.course,
                "semester": student.semester,
                "division": student.division,
                "academic_year": student.academic_year
            },
            "stats": {
                "overall_attendance": attendance_percentage,
                "attended_lectures": attended_lectures,
                "total_lectures": total_lectures,
                "total_subjects": len(subject_attendance),
                "total_notices": len(notices)
            },
            "subjects": subject_attendance,
            "notices": notices
        }), 200

    except Exception as e:
        return jsonify({"success": False, "message": f"Error fetching student dashboard: {str(e)}"}), 500


@api_bp.route('/student/attendance', methods=['GET'])
def api_student_attendance():
    """Detailed Subject-wise and Lecture-wise attendance for a student."""
    student_id = request.args.get('student_id', type=int)
    if not student_id:
        return jsonify({"success": False, "message": "student_id required"}), 400

    try:
        att_dashboard = get_student_attendance_dashboard(student_id)
        return jsonify({
            "success": True,
            "attendance": att_dashboard
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@api_bp.route('/student/results', methods=['GET'])
def api_student_results():
    """Returns student internal marks."""
    student_id = request.args.get('student_id', type=int)
    if not student_id:
        return jsonify({"success": False, "message": "student_id required"}), 400

    try:
        student = get_student_by_id(student_id)
        if not student:
            return jsonify({"success": False, "message": "Student not found"}), 404

        marks_records = InternalMark.query.filter_by(student_id=student_id, semester=student.semester).all()
        results = []
        for mr in marks_records:
            subj = Subject.query.get(mr.subject_id)
            results.append({
                "subject_code": subj.subject_code if subj else '',
                "subject_name": subj.subject_name if subj else 'Unknown',
                "test1": mr.test1,
                "test2": mr.test2,
                "internal_exam": mr.internal_exam,
                "attendance": mr.attendance,
                "total": mr.total_internal,
                "percentage": round((mr.total_internal / 30.0) * 100, 1) if mr.total_internal else 0.0
            })

        return jsonify({
            "success": True,
            "semester": student.semester,
            "results": results
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"}), 500


@api_bp.route('/student/notices', methods=['GET'])
def api_student_notices():
    """Public and student notices."""
    try:
        raw_notices = get_public_notices(limit=25)
        notices = [{
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "category": getattr(n, 'target_role', 'all'),
            "date": n.created_at.strftime('%d %b %Y') if getattr(n, 'created_at', None) else '',
            "file_path": getattr(n, 'file_path', None)
        } for n in raw_notices]
        return jsonify({"success": True, "notices": notices}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@api_bp.route('/student/scan', methods=['POST'])
def api_student_scan_attendance():
    """Mark attendance by scanning lecture session QR code or entering session code."""
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    student_id = data.get('student_id')
    session_id = data.get('session_id')

    if not student_id:
        return jsonify({"success": False, "message": "student_id is required"}), 400

    try:
        success, msg = mark_student_qr_attendance(student_id, session_id=session_id)
        return jsonify({
            "success": success,
            "message": msg
        }), (200 if success else 400)
    except Exception as e:
        return jsonify({"success": False, "message": f"Scan error: {str(e)}"}), 500


# ------------------------------------------------------------------------------
# 4. Faculty Endpoints
# ------------------------------------------------------------------------------
@api_bp.route('/faculty/dashboard', methods=['GET'])
def api_faculty_dashboard():
    """Faculty dashboard data: assigned subjects, classes today, total students."""
    faculty_id = request.args.get('faculty_id', type=int)
    if not faculty_id:
        return jsonify({"success": False, "message": "faculty_id required"}), 400

    try:
        faculty = get_faculty_by_id(faculty_id)
        if not faculty:
            return jsonify({"success": False, "message": "Faculty not found"}), 404

        dash_data = get_faculty_dashboard_data(faculty_id)
        assigned_subjects = get_faculty_assigned_subjects(faculty_id)

        formatted_subjects = []
        for s in assigned_subjects:
            formatted_subjects.append({
                "assignment_id": s.id,
                "subject_id": s.subject_id,
                "subject_name": s.subject.subject_name if s.subject else 'Unknown',
                "subject_code": s.subject.subject_code if s.subject else '',
                "semester": s.semester,
                "division": s.division,
                "credits": s.subject.credits if s.subject else 4,
                "course": s.subject.course if s.subject else '',
                "student_count": 60
            })

        return jsonify({
            "success": True,
            "faculty": {
                "id": faculty.id,
                "name": faculty.name,
                "faculty_code": faculty.faculty_code,
                "department": faculty.department,
                "designation": faculty.designation,
                "email": faculty.email,
                "mobile": faculty.mobile
            },
            "stats": {
                "total_assigned_classes": len(formatted_subjects),
                "total_students": getattr(dash_data, 'total_students', 0) if dash_data else 120,
                "total_sessions_conducted": getattr(dash_data, 'total_sessions', 0) if dash_data else 35
            },
            "assigned_subjects": formatted_subjects
        }), 200

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@api_bp.route('/faculty/subjects', methods=['GET'])
def api_faculty_subjects():
    """Get all assigned subjects for faculty."""
    faculty_id = request.args.get('faculty_id', type=int)
    if not faculty_id:
        return jsonify({"success": False, "message": "faculty_id required"}), 400

    try:
        assigned = get_faculty_assigned_subjects(faculty_id)
        result = []
        for s in assigned:
            result.append({
                "assignment_id": s.id,
                "subject_id": s.subject_id,
                "subject_name": s.subject.subject_name if s.subject else '',
                "subject_code": s.subject.subject_code if s.subject else '',
                "semester": s.semester,
                "division": s.division,
                "credits": s.subject.credits if s.subject else 4,
                "internal_marks": getattr(s.subject, 'internal_marks', 30),
                "external_marks": getattr(s.subject, 'external_marks', 70)
            })
        return jsonify({"success": True, "subjects": result}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@api_bp.route('/faculty/students', methods=['GET'])
def api_faculty_students():
    """Fetch students for a specific subject & division assigned to faculty."""
    faculty_id = request.args.get('faculty_id', type=int)
    subject_id = request.args.get('subject_id', type=int)
    division = request.args.get('division', type=str)
    semester = request.args.get('semester', type=int)

    if not all([faculty_id, subject_id, division]):
        return jsonify({"success": False, "message": "faculty_id, subject_id, and division required"}), 400

    try:
        students = get_faculty_marks_students(faculty_id, subject_id, division, semester=semester)
        result = []
        for st in students:
            result.append({
                "id": st.id,
                "roll_number": st.roll_number,
                "name": st.name,
                "enrollment_no": st.enrollment_no,
                "division": st.division,
                "email": st.email
            })
        return jsonify({"success": True, "students": result}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@api_bp.route('/faculty/attendance/save', methods=['POST'])
def api_faculty_save_attendance():
    """Save lecture session attendance from mobile app."""
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    faculty_id = data.get('faculty_id')
    subject_id = data.get('subject_id')
    division = data.get('division')
    semester = data.get('semester')
    academic_year = data.get('academic_year', '2026-27')
    lecture_date = data.get('lecture_date', datetime.utcnow().strftime('%Y-%m-%d'))
    lecture_no = data.get('lecture_no', 'Lecture 1')
    student_statuses = data.get('student_statuses', {}) # dict of { "student_id": "Present"|"Absent" }

    if not all([faculty_id, subject_id, division, semester]):
        return jsonify({"success": False, "message": "Missing required lecture details"}), 400

    try:
        saved_session = save_lecture_session_attendance(
            faculty_id=faculty_id,
            subject_id=subject_id,
            division=division,
            semester=semester,
            academic_year=academic_year,
            lecture_date=lecture_date,
            lecture_no=lecture_no,
            student_statuses=student_statuses,
            action_type='submit'
        )
        return jsonify({
            "success": True,
            "message": "Attendance marked and synchronized with Web successfully!",
            "session_id": saved_session.id if saved_session else None
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": f"Failed to save attendance: {str(e)}"}), 500


@api_bp.route('/faculty/marks/save', methods=['POST'])
def api_faculty_save_marks():
    """Save student internal assessment marks."""
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    student_id = data.get('student_id')
    subject_id = data.get('subject_id')
    semester = data.get('semester')
    test1 = data.get('test1')
    test2 = data.get('test2')
    internal_exam = data.get('internal_exam')
    attendance = data.get('attendance')

    if not (student_id and subject_id and semester):
        return jsonify({"success": False, "message": "student_id, subject_id, semester required"}), 400

    try:
        mark = InternalMark.query.filter_by(student_id=student_id, subject_id=subject_id, semester=semester).first()
        if not mark:
            mark = InternalMark(student_id=student_id, subject_id=subject_id, semester=semester, academic_year='2026-27')
            db.session.add(mark)

        if test1 is not None: mark.test1 = float(test1)
        if test2 is not None: mark.test2 = float(test2)
        if internal_exam is not None: mark.internal_exam = float(internal_exam)
        if attendance is not None: mark.attendance = float(attendance)

        db.session.commit()
        return jsonify({"success": True, "message": "Marks saved and synchronized!"}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@api_bp.route('/faculty/notices/create', methods=['POST'])
def api_faculty_create_notice():
    """Create a new notice from Faculty portal."""
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    title = data.get('title')
    message = data.get('message')
    faculty_id = data.get('faculty_id')

    if not title or not message:
        return jsonify({"success": False, "message": "Title and message are required"}), 400

    try:
        notice = create_notification(
            title=title,
            message=message,
            category='General',
            posted_by_role='Faculty',
            faculty_id=faculty_id,
            target_audience='Student',
            priority='Normal'
        )
        if not notice:
            return jsonify({"success": False, "message": "Failed to create notice."}), 500
        return jsonify({"success": True, "message": "Notice published successfully!"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ------------------------------------------------------------------------------
# 5. Admin Endpoints
# ------------------------------------------------------------------------------
@api_bp.route('/admin/dashboard', methods=['GET'])
def api_admin_dashboard():
    """Returns top-level campus metrics for Admin."""
    try:
        dash_data = get_admin_dashboard_data()
        return jsonify({
            "success": True,
            "metrics": {
                "total_students": dash_data.get('total_students', 0),
                "total_faculty": dash_data.get('total_faculty', 0),
                "total_subjects": dash_data.get('total_subjects', 0),
                "total_sessions": dash_data.get('total_sessions', 0),
                "avg_attendance": dash_data.get('avg_attendance', 0.0),
                "min_overall_threshold": dash_data.get('min_overall_threshold', 75.0)
            },
            "recent_sessions": dash_data.get('recent_sessions', []),
            "semester_distribution": dash_data.get('semester_distribution', [])
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@api_bp.route('/admin/students', methods=['GET'])
def api_admin_get_students():
    """Get students list with optional semester and search filter."""
    semester = request.args.get('semester', type=int)
    search = request.args.get('search', type=str, default='').strip().lower()

    try:
        query = Student.query.filter_by(status='Active')
        if semester:
            query = query.filter_by(semester=semester)

        all_students = query.order_by(db.cast(Student.roll_number, db.Integer).asc()).all()
        result = []
        for s in all_students:
            if search:
                name_match = search in (s.name or '').lower()
                roll_match = search in (s.roll_number or '').lower()
                enr_match = search in (s.enrollment_no or '').lower()
                if not (name_match or roll_match or enr_match):
                    continue

            result.append({
                "id": s.id,
                "name": s.name,
                "roll_number": s.roll_number,
                "enrollment_no": s.enrollment_no,
                "course": s.course,
                "semester": s.semester,
                "division": s.division,
                "email": s.email,
                "mobile": s.mobile
            })

        return jsonify({"success": True, "students": result, "total": len(result)}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@api_bp.route('/admin/faculty', methods=['GET'])
def api_admin_get_faculty():
    """Get faculty list with details."""
    try:
        faculty_list = Faculty.query.filter_by(status='Active').all()
        result = []
        for f in faculty_list:
            result.append({
                "id": f.id,
                "faculty_code": f.faculty_code,
                "name": f.name,
                "email": f.email,
                "department": f.department,
                "designation": f.designation,
                "mobile": f.mobile
            })
        return jsonify({"success": True, "faculty": result, "total": len(result)}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@api_bp.route('/admin/subjects', methods=['GET'])
def api_admin_get_subjects():
    """Get subject directory."""
    semester = request.args.get('semester', type=int)
    try:
        query = Subject.query.filter_by(status='Active')
        if semester:
            query = query.filter_by(semester=semester)

        subjects = query.order_by(Subject.semester.asc(), Subject.subject_code.asc()).all()
        result = []
        for sb in subjects:
            result.append({
                "id": sb.id,
                "subject_code": sb.subject_code,
                "subject_name": sb.subject_name,
                "course": sb.course,
                "semester": sb.semester,
                "credits": sb.credits,
                "internal_marks": sb.internal_marks,
                "external_marks": sb.external_marks,
                "total_marks": sb.total_marks
            })
        return jsonify({"success": True, "subjects": result, "total": len(result)}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@api_bp.route('/admin/notices/create', methods=['POST'])
def api_admin_create_notice():
    """Create a new notice from Admin portal."""
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    title = data.get('title')
    message = data.get('message')
    target_role = data.get('target_role', 'all')
    admin_id = data.get('admin_id')

    if not title or not message:
        return jsonify({"success": False, "message": "Title and message are required"}), 400

    try:
        target_aud = target_role if target_role in ('All', 'Guest', 'Faculty', 'Student') else 'All'
        notice = create_notification(
            title=title,
            message=message,
            category='General',
            posted_by_role='Admin',
            admin_id=admin_id,
            target_audience=target_aud,
            priority='Normal'
        )
        if not notice:
            return jsonify({"success": False, "message": "Failed to create notice."}), 500
        return jsonify({"success": True, "message": "Notice published to entire campus!"}), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@api_bp.route('/college/settings', methods=['GET'])
def api_college_settings():
    """Get college settings and academic config."""
    try:
        college = get_college_settings()
        return jsonify({
            "success": True,
            "settings": {
                "college_name": getattr(college, 'college_name', 'CampusSync Institute of Technology'),
                "college_code": getattr(college, 'college_code', 'CSIT-001'),
                "college_type": getattr(college, 'college_type', 'BCA'),
                "current_academic_year": getattr(college, 'current_academic_year', '2026-27'),
                "current_semester_cycle": getattr(college, 'current_semester_cycle', 'Odd Semester'),
                "min_overall_attendance": getattr(college, 'min_overall_attendance', 75.0),
                "email": getattr(college, 'email', 'admin@campussync.edu')
            }
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


# ------------------------------------------------------------------------------
# 16. Dynamic Notification Bell Feed & Unread Seen Tracking
# ------------------------------------------------------------------------------
@api_bp.route('/notifications/unread-feed', methods=['GET'])
def api_unread_notifications_feed():
    """
    Returns unread count and latest notifications feed for the current logged-in user.
    Used by topbar notification bell for dynamic badges and dropdown previews.
    """
    user_role = None
    user_id = None

    portal_req = (request.args.get('portal') or request.args.get('role') or '').lower()

    if portal_req == 'student' and session.get('student_id'):
        user_role = 'Student'
        user_id = session['student_id']
    elif portal_req == 'faculty' and session.get('faculty_id'):
        user_role = 'Faculty'
        user_id = session['faculty_id']
    elif portal_req == 'admin' and session.get('admin_id'):
        user_role = 'Admin'
        user_id = session['admin_id']
    else:
        # Fallback to session user_role or session keys
        sess_role = (session.get('user_role') or '').lower()
        if sess_role == 'student' and session.get('student_id'):
            user_role = 'Student'
            user_id = session['student_id']
        elif sess_role == 'faculty' and session.get('faculty_id'):
            user_role = 'Faculty'
            user_id = session['faculty_id']
        elif sess_role == 'admin' and session.get('admin_id'):
            user_role = 'Admin'
            user_id = session['admin_id']
        elif 'student_id' in session:
            user_role = 'Student'
            user_id = session['student_id']
        elif 'faculty_id' in session:
            user_role = 'Faculty'
            user_id = session['faculty_id']
        elif 'admin_id' in session:
            user_role = 'Admin'
            user_id = session['admin_id']
        else:
            role_param = request.args.get('role')
            uid_param = request.args.get('user_id', type=int)
            if role_param and uid_param:
                user_role = role_param.capitalize()
                user_id = uid_param

    if not user_role or not user_id:
        return jsonify({
            "success": True,
            "unread_count": 0,
            "notifications": [],
            "authenticated": False
        }), 200

    try:
        limit = request.args.get('limit', default=8, type=int)
        feed = get_user_notifications_feed(user_role, user_id, limit=limit)
        return jsonify({
            "success": True,
            "user_role": user_role,
            "user_id": user_id,
            "unread_count": feed["unread_count"],
            "notifications": feed["notifications"],
            "authenticated": True
        }), 200
    except Exception as e:
        from extensions import db
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


@api_bp.route('/notifications/mark-read', methods=['POST'])
def api_mark_notification_read():
    """
    Marks a single notice or all notices as read for the logged-in user.
    Instant WhatsApp/Instagram-like badge decrement and clearing.
    """
    user_role = None
    user_id = None

    data = request.get_json(silent=True) or request.form.to_dict() or {}
    portal_req = (data.get('portal') or data.get('role') or request.args.get('portal') or '').lower()

    if portal_req == 'student' and session.get('student_id'):
        user_role = 'Student'
        user_id = session['student_id']
    elif portal_req == 'faculty' and session.get('faculty_id'):
        user_role = 'Faculty'
        user_id = session['faculty_id']
    elif portal_req == 'admin' and session.get('admin_id'):
        user_role = 'Admin'
        user_id = session['admin_id']
    else:
        sess_role = (session.get('user_role') or '').lower()
        if sess_role == 'student' and session.get('student_id'):
            user_role = 'Student'
            user_id = session['student_id']
        elif sess_role == 'faculty' and session.get('faculty_id'):
            user_role = 'Faculty'
            user_id = session['faculty_id']
        elif sess_role == 'admin' and session.get('admin_id'):
            user_role = 'Admin'
            user_id = session['admin_id']
        elif 'student_id' in session:
            user_role = 'Student'
            user_id = session['student_id']
        elif 'faculty_id' in session:
            user_role = 'Faculty'
            user_id = session['faculty_id']
        elif 'admin_id' in session:
            user_role = 'Admin'
            user_id = session['admin_id']
        else:
            role_param = data.get('role')
            uid_param = data.get('user_id')
            if role_param and uid_param:
                user_role = str(role_param).capitalize()
                user_id = int(uid_param)

    if not user_role or not user_id:
        return jsonify({"success": False, "message": "Authentication required."}), 401

    try:
        mark_all = data.get('mark_all', False)
        if str(mark_all).lower() in ('true', '1', 'yes'):
            marked_count = mark_all_notifications_as_read(user_role, user_id)
            feed = get_user_notifications_feed(user_role, user_id, limit=1)
            return jsonify({
                "success": True,
                "marked_all": True,
                "marked_count": marked_count,
                "unread_count": feed["unread_count"]
            }), 200

        notification_id = data.get('notification_id')
        if not notification_id:
            return jsonify({"success": False, "message": "notification_id is required"}), 400

        notification_id = int(notification_id)
        mark_notification_as_read(notification_id, user_role, user_id)
        feed = get_user_notifications_feed(user_role, user_id, limit=1)

        return jsonify({
            "success": True,
            "notification_id": notification_id,
            "unread_count": feed["unread_count"]
        }), 200
    except Exception as e:
        from extensions import db
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500


# ------------------------------------------------------------------------------
# System: Database Schema Migration Endpoint
# ------------------------------------------------------------------------------
@api_bp.route('/system/migrate-db', methods=['GET', 'POST'])
def api_migrate_db():
    """Manual or automated endpoint to run database migrations on Railway."""
    try:
        from app import run_all_database_migrations
        res = run_all_database_migrations()
        return jsonify({
            "success": True,
            "migrated_count": len(res.get("migrated", [])),
            "migrated_items": res.get("migrated", []),
            "error_count": len(res.get("errors", [])),
            "errors": res.get("errors", [])
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


