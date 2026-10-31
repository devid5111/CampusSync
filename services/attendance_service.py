"""
CampusSync ERP - Attendance Service
===================================
File: services/attendance_service.py

Provides authoritative backend logic for fetching subject-specific student attendance
and calculating HNGU Internal Assessment Attendance /5 scores.
"""

from extensions import db
from models import (
    AttendanceRecord, Student, Subject, FacultySubjectAssignment,
    InternalMark, LectureAttendanceSession, LectureAttendanceStudent
)
from datetime import datetime


def get_subject_attendance_summary(student_ids, subject_id, semester, academic_year, division=None):
    """
    Retrieves subject-specific attendance percentage and converts to HNGU Internal Attendance /5 score.

    Parameters:
        student_ids (list[int]): List of student IDs
        subject_id (int): Subject ID
        semester (int): Semester number
        academic_year (str): Academic year (e.g., '2026-27')
        division (str, optional): Class division (e.g., 'A')

    Validation:
    - Subject-specific attendance query for student + subject + semester + academic_year + division.
    - Percentage = (attended_lectures / total_lectures) * 100.0.
    - Proportional Conversion = round((percentage / 100.0) * 5.0, 2).
    - If attendance data is missing/unavailable: has_attendance = False, label = "N/A".

    Returns:
        dict: Mapping of student_id -> attendance info dictionary
    """
    if not student_ids:
        return {}

    try:
        subject_id = int(subject_id)
        semester = int(semester)
    except (ValueError, TypeError):
        return {}

    ay = str(academic_year).strip()

    results = {}
    for st_id in student_ids:
        try:
            st_id_int = int(st_id)
        except (ValueError, TypeError):
            continue

        query = AttendanceRecord.query.filter_by(
            student_id=st_id_int,
            subject_id=subject_id,
            semester=semester,
            academic_year=ay
        )

        if division:
            query = query.filter_by(division=str(division).strip().upper())

        record = query.first()

        if record and record.total_lectures > 0:
            pct = round((record.attended_lectures / float(record.total_lectures)) * 100.0, 2)
            pct = min(100.0, max(0.0, pct))
            # HNGU Proportional Conversion to /5
            score = round((pct / 100.0) * 5.0, 2)
            score = min(5.0, max(0.0, score))

            results[st_id_int] = {
                "has_attendance": True,
                "percentage": pct,
                "score": score,
                "label": f"{pct}% ({score:.2f}/5)",
                "total_lectures": record.total_lectures,
                "attended_lectures": record.attended_lectures
            }
        else:
            results[st_id_int] = {
                "has_attendance": False,
                "percentage": None,
                "score": None,
                "label": "N/A",
                "total_lectures": 0,
                "attended_lectures": 0
            }

    return results


def verify_attendance_ready(subject_id, semester, academic_year, division, marks_list=None):
    """
    Validates server-side whether attendance data is ready/populated for the given
    Subject + Division + Semester + Academic Year combination.

    Required Security Check:
    - Protects Save Bulk Marks, Excel Export, and PDF Export endpoints.
    - Ensures faculty cannot bypass frontend button states via direct API calls or direct URL navigation.

    Returns:
        tuple: (True, None) if attendance is ready, or (False, error_message) if attendance must be filled first.
    """
    if not subject_id or not division:
        return False, "Subject ID and Division are required."

    try:
        subject_id = int(subject_id)
    except (ValueError, TypeError):
        return False, "Invalid Subject ID."

    subject = db.session.get(Subject, subject_id)
    if not subject:
        return False, "Subject not found."

    # Practical subjects do not use HNGU theory attendance component
    if subject.subject_type == 'Practical':
        return True, None

    ay = str(academic_year).strip() if academic_year else "2026-27"
    try:
        sem = int(semester) if semester else subject.semester
    except (ValueError, TypeError):
        sem = subject.semester
    div = str(division).strip().upper()

    # Case A: Bulk save API payload validation
    if marks_list is not None and len(marks_list) > 0:
        # For Theory subjects, EVERY student submitted in marks_list must have a populated attendance value
        unfilled = any(
            m.get('attendance') is None or str(m.get('attendance')).strip() == ''
            for m in marks_list
        )
        if unfilled:
            return False, "⚠ Attendance Not Filled: Please click 'FILL ATTENDANCE' first before saving internal marks."
        return True, None

    # Case B: Export GET routes (Excel / PDF) validation
    # 1. Check if AttendanceRecords exist in DB
    records_count = AttendanceRecord.query.filter_by(
        subject_id=subject_id,
        semester=sem,
        academic_year=ay,
        division=div
    ).filter(AttendanceRecord.total_lectures > 0).count()

    if records_count > 0:
        return True, None

    # 2. Check if existing InternalMark records in DB have attendance scores for active division students
    active_students = Student.query.filter_by(
        semester=sem,
        division=div,
        status='Active'
    ).all()

    if not active_students:
        return True, None

    student_ids = [s.id for s in active_students]

    marks_with_att = InternalMark.query.filter(
        InternalMark.subject_id == subject_id,
        InternalMark.semester == sem,
        InternalMark.academic_year == ay,
        InternalMark.student_id.in_(student_ids),
        InternalMark.attendance.isnot(None)
    ).count()

    if marks_with_att >= len(active_students):
        return True, None

    return False, "⚠ Attendance Not Filled: Please click 'FILL ATTENDANCE' first before exporting internal marks."


def get_lecture_session_attendance(faculty_id, subject_id, division, semester, academic_year, lecture_date, lecture_no="Lecture 1"):
    """
    Retrieves existing lecture session attendance for a specific date, subject, division, and lecture_no.
    """
    if not subject_id or not division or not lecture_date:
        return None

    try:
        sub_id = int(subject_id)
        sem = int(semester)
        if isinstance(lecture_date, str):
            l_date = datetime.strptime(lecture_date.strip(), "%Y-%m-%d").date()
        else:
            l_date = lecture_date
    except (ValueError, TypeError):
        return None

    ay = str(academic_year).strip() if academic_year else "2026-27"
    div = str(division).strip().upper()
    lec_no = str(lecture_no).strip() if lecture_no else "Lecture 1"

    session_rec = LectureAttendanceSession.query.filter_by(
        subject_id=sub_id,
        semester=sem,
        division=div,
        academic_year=ay,
        lecture_date=l_date,
        lecture_no=lec_no
    ).first()

    if not session_rec:
        return None

    statuses = {entry.student_id: entry.status for entry in session_rec.student_entries}

    return {
        "session_id": session_rec.id,
        "status": session_rec.status, # 'Draft' or 'Submitted'
        "lecture_date": str(session_rec.lecture_date),
        "lecture_no": session_rec.lecture_no,
        "statuses": statuses
    }



def validate_session_time_and_conflict(semester, division, lecture_date, start_time, end_time, session_id=None, subject_id=None):
    """
    Validates:
    1. start_time < end_time
    2. Within College Operating Hours (college_start_time to college_end_time from CollegeSetting).
    3. No overlapping lecture/lab session for the same semester and division on that date.
       Overlap condition: (new_start < existing_end) and (new_end > existing_start)
    Returns: (is_valid: bool, error_message: str or None)
    """
    import re
    from datetime import datetime
    from models import CollegeSetting, LectureAttendanceSession

    if not start_time or not end_time:
        return True, None

    start_time = str(start_time).strip()
    end_time = str(end_time).strip()

    if start_time >= end_time:
        return False, f"Invalid Timing: Start Time ({start_time}) must be earlier than End Time ({end_time})."

    # 1. College Operating Hours Check
    college = CollegeSetting.query.first()
    col_start = (college.college_start_time if college and college.college_start_time else "10:00").strip()
    col_end = (college.college_end_time if college and college.college_end_time else "17:00").strip()

    if start_time < col_start or end_time > col_end:
        return False, f"Schedule Warning: Selected time ({start_time} - {end_time}) falls outside College Operating Hours ({col_start} - {col_end}). All sessions must be scheduled between {col_start} and {col_end}."

    # 2. Class Timetable Overlap Conflict Check
    try:
        if isinstance(lecture_date, str):
            l_date = datetime.strptime(lecture_date.strip(), "%Y-%m-%d").date()
        else:
            l_date = lecture_date
    except Exception:
        l_date = None

    if l_date and semester and division:
        existing_sessions = LectureAttendanceSession.query.filter_by(
            semester=int(semester),
            division=str(division).strip().upper(),
            lecture_date=l_date
        ).all()

        for es in existing_sessions:
            if session_id and es.id == int(session_id):
                continue
            if subject_id and es.subject_id == int(subject_id) and es.start_time == start_time and es.end_time == end_time:
                continue

            es_start = es.start_time
            es_end = es.end_time
            if not es_start or not es_end:
                m = re.search(r'\(([\d:]+)\s*-\s*([\d:]+)\)', es.lecture_no or '')
                if m:
                    es_start, es_end = m.group(1), m.group(2)

            if es_start and es_end:
                # Overlap check: (new_start < existing_end) and (new_end > existing_start)
                if (start_time < es_end) and (end_time > es_start):
                    sub_title = es.subject.subject_name if es.subject else "another subject"
                    fac_name = es.faculty.full_name if es.faculty else "Faculty"
                    return False, f"Schedule Conflict: Semester {semester} (Div {division}) already has a {es.session_type or 'session'} for [{sub_title}] ({fac_name}) scheduled from {es_start} to {es_end} on {lecture_date}. Overlapping sessions are strictly prohibited for the same class."

    return True, None


def save_lecture_session_attendance(faculty_id, subject_id, division, semester, academic_year, lecture_date, lecture_no, student_statuses, action_type='draft', session_type='Lecture', start_time=None, end_time=None):
    """
    Saves or updates daily lecture attendance session and updates cumulative AttendanceRecord table when finalized.
    """
    if not faculty_id or not subject_id or not division or not lecture_date:
        return False, "Missing required parameters."

    try:
        fac_id = int(faculty_id)
        sub_id = int(subject_id)
        sem = int(semester)
        if isinstance(lecture_date, str):
            l_date = datetime.strptime(lecture_date.strip(), "%Y-%m-%d").date()
        else:
            l_date = lecture_date
    except (ValueError, TypeError):
        return False, "Invalid parameters or date format."

    ay = str(academic_year).strip() if academic_year else "2026-27"
    div = str(division).strip().upper()
    lec_no = str(lecture_no).strip() if lecture_no else "Lecture 1"
    is_final = (str(action_type).strip().lower() == 'final')

    # Verify Faculty Assignment Authorization
    assignment = FacultySubjectAssignment.query.filter_by(
        faculty_id=fac_id,
        subject_id=sub_id,
        division=div,
        status='Active'
    ).first()

    if not assignment:
        return False, "Access Denied: You are not assigned to this subject/division."

    # Timetable Conflict & College Hours Validation
    import re
    if not start_time or not end_time:
        m = re.search(r'\(([\d:]+)\s*-\s*([\d:]+)\)', lec_no)
        if m:
            start_time, end_time = m.group(1), m.group(2)
        m_type = re.match(r'(Lecture|Lab)', lec_no)
        if m_type:
            session_type = m_type.group(1)

    if start_time and end_time:
        is_valid, val_err = validate_session_time_and_conflict(
            semester=sem,
            division=div,
            lecture_date=l_date,
            start_time=start_time,
            end_time=end_time,
            subject_id=sub_id
        )
        if not is_valid:
            return False, val_err
        lec_no = f"{session_type} ({start_time} - {end_time})"

    try:
        # Find or create session
        session_rec = LectureAttendanceSession.query.filter_by(
            subject_id=sub_id,
            semester=sem,
            division=div,
            academic_year=ay,
            lecture_date=l_date,
            lecture_no=lec_no
        ).first()

        if not session_rec:
            session_rec = LectureAttendanceSession(
                faculty_id=fac_id,
                subject_id=sub_id,
                semester=sem,
                division=div,
                academic_year=ay,
                lecture_date=l_date,
                lecture_no=lec_no,
                status='Submitted' if is_final else 'Draft',
                is_qr_active=0
            )
            if is_final:
                session_rec.qr_session_expires_at = datetime.utcnow()
            db.session.add(session_rec)
            db.session.flush()
        else:
            if session_rec.status == 'Submitted':
                return False, "This attendance register has already been Final Submitted & Locked. It is strictly read-only and cannot be modified."
            session_rec.faculty_id = fac_id
            session_rec.status = 'Submitted' if is_final else 'Draft'
            if is_final:
                session_rec.is_qr_active = 0
                session_rec.qr_session_expires_at = datetime.utcnow()
            elif str(action_type).strip().lower() == 'draft':
                session_rec.is_qr_active = 0

        # Update or create student entries
        for st_id_str, status_val in student_statuses.items():
            try:
                st_id = int(st_id_str)
            except (ValueError, TypeError):
                continue

            status_norm = 'Present' if str(status_val).strip().upper() in ['P', 'PRESENT'] else 'Absent'

            st_entry = LectureAttendanceStudent.query.filter_by(
                session_id=session_rec.id,
                student_id=st_id
            ).first()

            if not st_entry:
                st_entry = LectureAttendanceStudent(
                    session_id=session_rec.id,
                    student_id=st_id,
                    status=status_norm
                )
                db.session.add(st_entry)
            else:
                st_entry.status = status_norm

        if is_final:
            # Strictly deactivate any active QR sessions for this lecture
            LectureAttendanceSession.query.filter_by(
                subject_id=sub_id,
                semester=sem,
                division=div,
                academic_year=ay,
                lecture_date=l_date,
                lecture_no=lec_no
            ).update({'is_qr_active': 0, 'qr_session_expires_at': datetime.utcnow()})

        db.session.commit()

        # If Final Submit, update cumulative AttendanceRecord for all submitted sessions for this subject/sem/div/ay
        if is_final:
            _recalculate_cumulative_attendance(sub_id, sem, div, ay)

        msg = "Attendance finalized and submitted successfully!" if is_final else "Attendance register saved as draft."
        return True, msg

    except Exception as e:
        db.session.rollback()
        return False, f"Failed to save attendance: {str(e)}"


def _recalculate_cumulative_attendance(subject_id, semester, division, academic_year):
    """
    Recalculates cumulative total_lectures and attended_lectures in AttendanceRecord table
    for all submitted lecture sessions of a given subject, semester, division, and academic year.
    """
    submitted_sessions = LectureAttendanceSession.query.filter_by(
        subject_id=subject_id,
        semester=semester,
        division=division,
        academic_year=academic_year,
        status='Submitted'
    ).all()

    total_submitted_lectures = len(submitted_sessions)

    # Gather students enrolled in this sem and div
    students = Student.query.filter_by(
        semester=semester,
        division=division,
        status='Active'
    ).all()

    session_ids = [s.id for s in submitted_sessions]

    for st in students:
        attended_cnt = 0
        if session_ids:
            attended_cnt = LectureAttendanceStudent.query.filter(
                LectureAttendanceStudent.session_id.in_(session_ids),
                LectureAttendanceStudent.student_id == st.id,
                LectureAttendanceStudent.status == 'Present'
            ).count()

        att_rec = AttendanceRecord.query.filter_by(
            student_id=st.id,
            subject_id=subject_id,
            semester=semester,
            academic_year=academic_year,
            division=division
        ).first()

        if not att_rec:
            att_rec = AttendanceRecord(
                student_id=st.id,
                subject_id=subject_id,
                semester=semester,
                academic_year=academic_year,
                division=division,
                total_lectures=total_submitted_lectures,
                attended_lectures=attended_cnt
            )
            db.session.add(att_rec)
        else:
            att_rec.total_lectures = total_submitted_lectures
            att_rec.attended_lectures = attended_cnt

    db.session.commit()



def get_student_attendance_dashboard(student_id):
    """
    Computes real-time attendance dashboard statistics for logged-in student.
    Returns:
        dict: {
            overall_percentage, overall_conducted, overall_attended, overall_missed, overall_status,
            subject_breakdown: [...],
            recent_logs: [...]
        }
    """
    student = Student.query.get(student_id)
    if not student:
        return None

    sem = student.semester or 1
    ay = student.academic_year or "2026-27"
    div = student.division or "A"

    import math
    from services.college_service import get_college_settings
    college = get_college_settings()

    min_overall = float(college.min_overall_attendance) if (college and getattr(college, 'min_overall_attendance', None) is not None) else 75.0
    min_subject = float(college.min_subject_attendance) if (college and getattr(college, 'min_subject_attendance', None) is not None) else 75.0
    warning_thresh = float(college.attendance_warning_threshold) if (college and getattr(college, 'attendance_warning_threshold', None) is not None) else 60.0

    subjects = Subject.query.filter_by(semester=sem, status='Active').order_by(Subject.subject_code.asc()).all()
    if not subjects:
        subjects = Subject.query.filter_by(semester=sem).order_by(Subject.subject_code.asc()).all()

    subject_breakdown = []
    total_conducted = 0
    total_attended = 0

    for sub in subjects:
        rec = AttendanceRecord.query.filter_by(
            student_id=student.id,
            subject_id=sub.id,
            semester=sem
        ).first()

        lec_total = rec.total_lectures if rec else 0
        lec_attended = rec.attended_lectures if rec else 0
        lec_missed = max(0, lec_total - lec_attended)

        if lec_total > 0:
            pct = round((lec_attended / float(lec_total)) * 100.0, 1)
            pct = min(100.0, max(0.0, pct))
            score = round((pct / 100.0) * 5.0, 2)
        else:
            pct = 0.0
            score = 0.0

        if lec_total == 0:
            status = 'Not Started'
            is_safe = True
            needed_safe = 0
            needed_warning = 0
        else:
            if pct >= min_subject:
                status = 'Safe'
                is_safe = True
                needed_safe = 0
            elif pct >= warning_thresh:
                status = 'Warning'
                is_safe = False
            else:
                status = 'Shortage'
                is_safe = False

            if pct < min_subject:
                t_sub = min_subject / 100.0
                denom = max(0.01, 1.0 - t_sub)
                needed_safe = math.ceil((t_sub * lec_total - lec_attended) / denom)
                needed_safe = max(1, needed_safe)
            else:
                needed_safe = 0

            if pct < warning_thresh:
                t_w = warning_thresh / 100.0
                denom_w = max(0.01, 1.0 - t_w)
                needed_warning = math.ceil((t_w * lec_total - lec_attended) / denom_w)
                needed_warning = max(1, needed_warning)
            else:
                needed_warning = 0

        total_conducted += lec_total
        total_attended += lec_attended

        subject_breakdown.append({
            "subject_id": sub.id,
            "subject_code": sub.subject_code,
            "subject_name": sub.subject_name,
            "subject_type": sub.subject_type or 'Theory',
            "total_lectures": lec_total,
            "attended_lectures": lec_attended,
            "missed_lectures": lec_missed,
            "percentage": pct,
            "score": score,
            "status": status,
            "is_safe": is_safe,
            "needed_safe": needed_safe,
            "needed_warning": needed_warning,
            "needed_75": needed_safe, # Backwards compatibility alias
            "needed_80": needed_safe
        })

    total_missed = max(0, total_conducted - total_attended)
    if total_conducted > 0:
        overall_pct = round((total_attended / float(total_conducted)) * 100.0, 1)
        overall_pct = min(100.0, max(0.0, overall_pct))
    else:
        overall_pct = 0.0

    # Overall Safe lecture calculation
    overall_needed_safe = 0
    overall_needed_warning = 0
    if total_conducted > 0:
        if overall_pct < min_overall:
            t_ov = min_overall / 100.0
            denom_ov = max(0.01, 1.0 - t_ov)
            overall_needed_safe = math.ceil((t_ov * total_conducted - total_attended) / denom_ov)
            overall_needed_safe = max(1, overall_needed_safe)
            overall_is_safe = False
        else:
            overall_needed_safe = 0
            overall_is_safe = True

        if overall_pct < warning_thresh:
            t_ow = warning_thresh / 100.0
            denom_ow = max(0.01, 1.0 - t_ow)
            overall_needed_warning = math.ceil((t_ow * total_conducted - total_attended) / denom_ow)
            overall_needed_warning = max(1, overall_needed_warning)
        else:
            overall_needed_warning = 0
    else:
        overall_is_safe = True
        overall_needed_safe = 0
        overall_needed_warning = 0

    if total_conducted == 0:
        overall_status = 'Not Started'
    elif overall_pct >= min_overall:
        overall_status = 'Safe'
    elif overall_pct >= warning_thresh:
        overall_status = 'Warning'
    else:
        overall_status = 'Shortage'

    # Query recent daily lecture logs for this student
    recent_entries = LectureAttendanceStudent.query.filter_by(student_id=student.id)\
        .join(LectureAttendanceSession, LectureAttendanceStudent.session_id == LectureAttendanceSession.id)\
        .order_by(LectureAttendanceSession.lecture_date.desc(), LectureAttendanceSession.id.desc())\
        .limit(15).all()

    recent_logs = []
    for entry in recent_entries:
        sess = entry.session
        sub_name = sess.subject.subject_name if sess.subject else "Subject"
        fac_name = sess.faculty.full_name if sess.faculty else "Faculty"
        recent_logs.append({
            "date": str(sess.lecture_date),
            "lecture_no": sess.lecture_no,
            "subject_code": sess.subject.subject_code if sess.subject else "",
            "subject_name": sub_name,
            "faculty_name": fac_name,
            "status": entry.status # 'Present' or 'Absent'
        })

    return {
        "student": student,
        "min_overall_attendance": min_overall,
        "min_subject_attendance": min_subject,
        "attendance_warning_threshold": warning_thresh,
        "overall_percentage": overall_pct,
        "overall_conducted": total_conducted,
        "overall_attended": total_attended,
        "overall_missed": total_missed,
        "overall_status": overall_status,
        "overall_is_safe": overall_is_safe,
        "overall_needed_safe": overall_needed_safe,
        "overall_needed_warning": overall_needed_warning,
        "overall_needed_75": overall_needed_safe,
        "overall_needed_80": overall_needed_safe,
        "subject_breakdown": subject_breakdown,
        "recent_logs": recent_logs
    }


def generate_student_attendance_pdf_bytes(student_id):
    """
    Generates official PDF report stream for student attendance.
    """
    import io
    from datetime import datetime
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable

    student = Student.query.get(student_id)
    if not student:
        return None
    att_data = get_student_attendance_dashboard(student_id)
    if not att_data:
        return None

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    primary_color = colors.HexColor("#0F172A")
    blue_color = colors.HexColor("#2563EB")
    light_bg = colors.HexColor("#F8FAFC")

    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=18, leading=22, textColor=primary_color)
    sub_style = ParagraphStyle('Sub', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=12, textColor=colors.HexColor("#64748B"))
    body_style = ParagraphStyle('Body', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=13, textColor=primary_color)
    hdr_cell = ParagraphStyle('HdrCell', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, leading=11, textColor=colors.white, alignment=1)
    txt_cell = ParagraphStyle('TxtCell', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=11, textColor=primary_color, alignment=1)

    story = []
    story.append(Paragraph("OFFICIAL STUDENT ATTENDANCE REPORT", title_style))
    story.append(Paragraph(f"CampusSync ERP &bull; Generated: {datetime.now().strftime('%d %b %Y, %I:%M %p')}", sub_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=blue_color, spaceBefore=4, spaceAfter=10))

    info_data = [
        [Paragraph(f"<b>Student Name:</b> {student.full_name}", body_style), Paragraph(f"<b>Enrollment No:</b> {student.enrollment_no}", body_style)],
        [Paragraph(f"<b>Course & Sem:</b> {student.course} - Sem {student.semester} (Div {student.division})", body_style), Paragraph(f"<b>Roll Number:</b> {student.roll_number or 'N/A'}", body_style)],
        [Paragraph(f"<b>Overall Attendance:</b> {att_data['overall_percentage']}%", body_style), Paragraph(f"<b>Eligibility Status:</b> {att_data['overall_status']}", body_style)]
    ]
    t_info = Table(info_data, colWidths=[270, 270])
    t_info.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), light_bg),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#e2e8f0")),
        ('PADDING', (0,0), (-1,-1), 6)
    ]))
    story.append(t_info)
    story.append(Spacer(1, 12))

    story.append(Paragraph("<b>Subject-Wise Attendance Summary</b>", ParagraphStyle('H2', parent=styles['Heading2'], fontSize=12, textColor=primary_color)))
    story.append(Spacer(1, 6))

    sub_table = [
        [Paragraph("Subject Code & Name", hdr_cell), Paragraph("Conducted", hdr_cell), Paragraph("Attended", hdr_cell), Paragraph("Missed", hdr_cell), Paragraph("Percentage", hdr_cell), Paragraph("Internal Score", hdr_cell)]
    ]

    for sub in att_data['subject_breakdown']:
        sub_table.append([
            Paragraph(f"<b>[{sub['subject_code']}]</b> {sub['subject_name']}", ParagraphStyle('LeftCell', parent=txt_cell, alignment=0)),
            Paragraph(str(sub['total_lectures']), txt_cell),
            Paragraph(str(sub['attended_lectures']), txt_cell),
            Paragraph(str(sub['missed_lectures']), txt_cell),
            Paragraph(f"<b>{sub['percentage']}%</b>", txt_cell),
            Paragraph(f"{sub['score']:.2f} / 5.0", txt_cell)
        ])

    t_sub = Table(sub_table, colWidths=[180, 65, 65, 65, 80, 85])
    t_sub.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), blue_color),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#e2e8f0")),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, light_bg]),
        ('PADDING', (0,0), (-1,-1), 5)
    ]))
    story.append(t_sub)
    story.append(Spacer(1, 14))

    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e2e8f0"), spaceBefore=10, spaceAfter=8))
    story.append(Paragraph("Verified & Issued by CampusSync Academic Portal &bull; Official Digital Record", ParagraphStyle('Foot', parent=sub_style, alignment=1)))

    doc.build(story)
    buffer.seek(0)
    return buffer


def mark_student_qr_attendance(student_id, session_id=None, token=None, lat=None, lng=None, device_fingerprint=None, device_model=None, request_approval=False, alert_id=None):
    return mark_student_qr_attendance_secure(
        student_id, session_id=session_id, token=token, lat=lat, lng=lng,
        device_fingerprint=device_fingerprint, device_model=device_model,
        request_approval=request_approval, alert_id=alert_id
    )

def _legacy_mark_student_qr_attendance(student_id, session_id=None):
    """
    Processes student QR Code scan to mark attendance as 'Present'.
    If session_id is not provided, finds active/latest session for student's semester & division.
    Updates LectureAttendanceStudent and recalculates cumulative AttendanceRecord.
    """
    student = Student.query.get(student_id)
    if not student:
        return False, "Student not found."

    sem = student.semester
    div = student.division

    if session_id:
        sess = LectureAttendanceSession.query.get(session_id)
    else:
        sess = LectureAttendanceSession.query.filter_by(
            semester=sem,
            division=div
        ).order_by(LectureAttendanceSession.id.desc()).first()

    if not sess:
        return False, "No active lecture session found for your semester & division."

    try:
        st_entry = LectureAttendanceStudent.query.filter_by(
            session_id=sess.id,
            student_id=student.id
        ).first()

        if not st_entry:
            st_entry = LectureAttendanceStudent(
                session_id=sess.id,
                student_id=student.id,
                status='Present'
            )
            db.session.add(st_entry)
        else:
            st_entry.status = 'Present'

        db.session.commit()

        # Recalculate summary
        _recalculate_cumulative_attendance(sess.subject_id, sess.semester, sess.division, sess.academic_year)

        sub_code = sess.subject.subject_code if sess.subject else "Lecture"
        return True, f"Attendance marked Present for {sub_code} ({sess.lecture_no})!"
    except Exception as e:
        db.session.rollback()
        return False, f"Failed to mark QR attendance: {str(e)}"


def get_semester_attendance_blacklist(semester, academic_year="2026-27", overall_threshold=None, subject_threshold=None, division='All'):
    """
    Identifies students who do not meet the attendance criteria for a given semester.
    A student is flagged as a Defaulter / Blacklisted if:
        overall_percentage < overall_threshold OR any subject percentage < subject_threshold
    """
    from services.college_service import get_college_settings
    college = get_college_settings()

    if overall_threshold is None:
        overall_threshold = float(college.min_overall_attendance) if (college and getattr(college, 'min_overall_attendance', None) is not None) else 75.0
    else:
        try:
            overall_threshold = float(overall_threshold)
        except (ValueError, TypeError):
            overall_threshold = 75.0

    if subject_threshold is None:
        subject_threshold = float(college.min_subject_attendance) if (college and getattr(college, 'min_subject_attendance', None) is not None) else 75.0
    else:
        try:
            subject_threshold = float(subject_threshold)
        except (ValueError, TypeError):
            subject_threshold = 75.0

    try:
        sem_num = int(semester) if semester is not None else 1
    except (ValueError, TypeError):
        sem_num = 1

    ay = str(academic_year).strip() if academic_year else "2026-27"
    target_div = str(division).strip() if division else 'All'

    # Get active subjects for this semester
    subjects = Subject.query.filter_by(semester=sem_num, status='Active').order_by(Subject.subject_code.asc()).all()
    if not subjects:
        subjects = Subject.query.filter_by(semester=sem_num).order_by(Subject.subject_code.asc()).all()

    # Query active students in this semester (and division if filtered)
    st_query = Student.query.filter_by(semester=sem_num, status='Active')
    if target_div and target_div != 'All':
        st_query = st_query.filter_by(division=target_div)
    students = st_query.order_by(db.cast(Student.roll_number, db.Integer).asc(), Student.enrollment_no.asc()).all()

    defaulters = []
    total_students = len(students)
    overall_percentages_sum = 0.0

    for st in students:
        total_conducted = 0
        total_attended = 0
        defaulter_subjects = []
        all_subject_stats = []

        for sub in subjects:
            rec = AttendanceRecord.query.filter_by(
                student_id=st.id,
                subject_id=sub.id,
                semester=sem_num
            ).first()

            lec_total = rec.total_lectures if rec else 0
            lec_attended = rec.attended_lectures if rec else 0
            pct = round((lec_attended / float(lec_total)) * 100.0, 1) if lec_total > 0 else 0.0

            total_conducted += lec_total
            total_attended += lec_attended

            is_sub_def = (lec_total > 0 and pct < subject_threshold)
            sub_info = {
                "subject_id": sub.id,
                "subject_code": sub.subject_code,
                "subject_name": sub.subject_name,
                "total_lectures": lec_total,
                "attended_lectures": lec_attended,
                "percentage": pct,
                "is_defaulter": is_sub_def
            }
            all_subject_stats.append(sub_info)

            if is_sub_def:
                defaulter_subjects.append(sub_info)

        overall_pct = round((total_attended / float(total_conducted)) * 100.0, 1) if total_conducted > 0 else 0.0
        overall_percentages_sum += overall_pct

        is_overall_defaulter = (total_conducted > 0 and overall_pct < overall_threshold)
        is_subject_defaulter = len(defaulter_subjects) > 0

        if is_overall_defaulter or is_subject_defaulter:
            reasons = []
            if is_overall_defaulter:
                reasons.append(f"Overall ({overall_pct}%) < {overall_threshold}%")
            if is_subject_defaulter:
                sub_codes = ", ".join([s['subject_code'] for s in defaulter_subjects])
                reasons.append(f"{len(defaulter_subjects)} Subject(s) [{sub_codes}] < {subject_threshold}%")

            defaulters.append({
                "student": st,
                "roll_number": st.roll_number or "-",
                "enrollment_no": st.enrollment_no,
                "full_name": st.full_name,
                "division": st.division or "A",
                "overall_percentage": overall_pct,
                "total_conducted": total_conducted,
                "total_attended": total_attended,
                "total_missed": max(0, total_conducted - total_attended),
                "defaulter_subjects": defaulter_subjects,
                "all_subjects": all_subject_stats,
                "reasons": reasons,
                "reason_text": " & ".join(reasons)
            })

    avg_overall_pct = round(overall_percentages_sum / float(total_students), 1) if total_students > 0 else 0.0
    eligible_count = max(0, total_students - len(defaulters))
    defaulter_rate = round((len(defaulters) / float(total_students)) * 100.0, 1) if total_students > 0 else 0.0

    return {
        "semester": sem_num,
        "academic_year": ay,
        "division": target_div,
        "overall_threshold": overall_threshold,
        "subject_threshold": subject_threshold,
        "total_students": total_students,
        "defaulters_count": len(defaulters),
        "eligible_count": eligible_count,
        "defaulter_rate": defaulter_rate,
        "avg_overall_pct": avg_overall_pct,
        "defaulters": defaulters,
        "subjects": subjects,
        "college": college
    }


def generate_attendance_blacklist_pdf(blacklist_data):
    """
    Generates official A4 institutional Attendance Defaulters / Blacklist PDF report.
    Includes balanced 3-column College Header, Criteria Box, properly-sized Defaulters Table,
    and a balanced 3-column footer (College Seal on left, System note in center, Principal Signature on right).
    """
    import io, os
    from datetime import datetime
    from reportlab.lib.pagesizes import A4, portrait
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable, Image as RLImage

    college = blacklist_data.get('college')
    sem = blacklist_data.get('semester', 1)
    ay = blacklist_data.get('academic_year', '2026-27')
    div = blacklist_data.get('division', 'All')
    overall_th = blacklist_data.get('overall_threshold', 75.0)
    subject_th = blacklist_data.get('subject_threshold', 75.0)
    defaulters = blacklist_data.get('defaulters', [])
    total_students = blacklist_data.get('total_students', 0)

    buffer = io.BytesIO()
    # A4: 595.27 x 841.89. Left 30, Right 30 -> Usable width = 535.27 pt
    doc = SimpleDocTemplate(
        buffer,
        pagesize=portrait(A4),
        rightMargin=30,
        leftMargin=30,
        topMargin=25,
        bottomMargin=25
    )

    styles = getSampleStyleSheet()
    navy_color = colors.HexColor("#0F3460")
    maroon_color = colors.HexColor("#991B1B")
    dark_color = colors.HexColor("#1E293B")
    muted_color = colors.HexColor("#64748B")
    light_bg = colors.HexColor("#F8FAFC")
    border_color = colors.HexColor("#CBD5E1")

    # Typography
    c_name = ParagraphStyle('ColName', fontName='Helvetica-Bold', fontSize=15, leading=18, textColor=navy_color, alignment=1)
    c_affil = ParagraphStyle('ColAffil', fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=colors.HexColor("#1E3A8A"), alignment=1)
    c_addr = ParagraphStyle('ColAddr', fontName='Helvetica', fontSize=7.5, leading=10, textColor=muted_color, alignment=1)

    report_title = ParagraphStyle('RepTitle', fontName='Helvetica-Bold', fontSize=11, leading=14, textColor=maroon_color, alignment=1)
    meta_style = ParagraphStyle('MetaStyle', fontName='Helvetica', fontSize=8, leading=11, textColor=muted_color, alignment=1)

    stat_lbl = ParagraphStyle('StatLbl', fontName='Helvetica-Bold', fontSize=7.5, leading=9.5, textColor=muted_color, alignment=1)
    stat_val = ParagraphStyle('StatVal', fontName='Helvetica-Bold', fontSize=9, leading=11.5, textColor=dark_color, alignment=1)
    stat_val_danger = ParagraphStyle('StatValD', fontName='Helvetica-Bold', fontSize=9.5, leading=11.5, textColor=maroon_color, alignment=1)

    th_cell = ParagraphStyle('ThCell', fontName='Helvetica-Bold', fontSize=7.5, leading=9.5, textColor=colors.white, alignment=1)
    td_center = ParagraphStyle('TdCenter', fontName='Helvetica', fontSize=7.5, leading=9.5, textColor=dark_color, alignment=1)
    td_bold_center = ParagraphStyle('TdBldCenter', fontName='Helvetica-Bold', fontSize=7.5, leading=9.5, textColor=dark_color, alignment=1)
    td_left = ParagraphStyle('TdLeft', fontName='Helvetica', fontSize=7.5, leading=9.5, textColor=dark_color, alignment=0)
    td_danger = ParagraphStyle('TdDanger', fontName='Helvetica-Bold', fontSize=7.5, leading=9.5, textColor=maroon_color, alignment=1)

    sig_title = ParagraphStyle('SigTitle', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=dark_color, alignment=1)
    sig_name = ParagraphStyle('SigName', fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=navy_color, alignment=1)
    sig_sub = ParagraphStyle('SigSub', fontName='Helvetica', fontSize=7, leading=9, textColor=muted_color, alignment=1)

    story = []

    # 1. College Header (Balanced 3-Column: 60pt Logo + 415pt Center Title + 60pt Spacer)
    col_name_str = (college.college_name if college and college.college_name else "CAMPUSSYNC COLLEGE").upper()
    col_affil_str = f"Department of {college.college_type}" if (college and getattr(college, 'college_type', None)) else ""
    col_addr = [college.address, college.city, college.state, college.pincode] if college else []
    col_addr_str = ", ".join([str(p).strip() for p in col_addr if p])

    uploads_dir = os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')), 'uploads', 'college')
    logo_flowable = None
    if college and college.logo and college.logo != 'default-logo.png':
        candidate = os.path.join(uploads_dir, college.logo)
        if os.path.exists(candidate):
            try:
                logo_flowable = RLImage(candidate, width=50, height=50)
                logo_flowable.hAlign = 'CENTER'
            except Exception:
                logo_flowable = None

    center_items = [
        Paragraph(col_name_str, c_name),
        Spacer(1, 1.5)
    ]
    if col_affil_str:
        center_items.append(Paragraph(col_affil_str, c_affil))
    if col_addr_str:
        center_items.append(Paragraph(col_addr_str, c_addr))

    if logo_flowable:
        header_table = Table([[logo_flowable, center_items, ""]], colWidths=[60, 415, 60])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 0), (0, 0), 'CENTER'),
            ('ALIGN', (1, 0), (1, 0), 'CENTER'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        story.append(header_table)
    else:
        for it in center_items:
            story.append(it)

    story.append(Spacer(1, 4))
    story.append(Paragraph("<b>ATTENDANCE DEFAULTERS / BLACKLIST REPORT</b>", report_title))
    today_formatted = datetime.now().strftime('%d-%m-%Y %I:%M %p')
    story.append(Paragraph(f"Academic Year: <b>{ay}</b> &bull; Semester: <b>Semester {sem}</b> &bull; Division: <b>{div}</b> &bull; Generated: {today_formatted}", meta_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=maroon_color, spaceBefore=3, spaceAfter=8))

    # 2. Filter Criteria & Summary Stats Box (5 Columns, width = 535)
    eligible_count = blacklist_data.get('eligible_count', max(0, total_students - len(defaulters)))
    stats_data = [
        [
            Paragraph("Overall Criteria", stat_lbl),
            Paragraph("Subject Criteria", stat_lbl),
            Paragraph("Total Students", stat_lbl),
            Paragraph("Defaulters Count", stat_lbl),
            Paragraph("Eligible Students", stat_lbl)
        ],
        [
            Paragraph(f"&lt; {overall_th}%", stat_val),
            Paragraph(f"&lt; {subject_th}%", stat_val),
            Paragraph(str(total_students), stat_val),
            Paragraph(str(len(defaulters)), stat_val_danger),
            Paragraph(str(eligible_count), stat_val)
        ]
    ]
    t_stats = Table(stats_data, colWidths=[107, 107, 107, 107, 107])
    t_stats.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), light_bg),
        ('GRID', (0, 0), (-1, -1), 0.5, border_color),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER')
    ]))
    story.append(t_stats)
    story.append(Spacer(1, 10))

    # 3. Defaulters Table (8 Columns, sum = 535 pt)
    # [28, 45, 95, 120, 32, 55, 105, 55]
    if not defaulters:
        empty_p = Paragraph("<br/><br/><b>✓ No Attendance Defaulters Found!</b><br/>All active students in Semester " + str(sem) + " meet or exceed the specified criteria.<br/><br/>", ParagraphStyle('Empty', parent=styles['Normal'], alignment=1, textColor=colors.HexColor("#15803D"), fontName='Helvetica-Bold', fontSize=11))
        story.append(empty_p)
    else:
        table_rows = [
            [
                Paragraph("Sr.", th_cell),
                Paragraph("Roll No", th_cell),
                Paragraph("Enrollment No", th_cell),
                Paragraph("Student Name", th_cell),
                Paragraph("Div", th_cell),
                Paragraph("Overall %", th_cell),
                Paragraph(f"Shortage (&lt;{subject_th}%)", th_cell),
                Paragraph("Attended / Total", th_cell)
            ]
        ]

        for idx, d in enumerate(defaulters, 1):
            sub_str_list = []
            for s in d['defaulter_subjects']:
                sub_str_list.append(f"<b>{s['subject_code']}</b>: {s['percentage']}% ({s['attended_lectures']}/{s['total_lectures']})")
            shortage_txt = "<br/>".join(sub_str_list) if sub_str_list else "<font color='#64748B'>None (Overall Shortage)</font>"

            table_rows.append([
                Paragraph(str(idx), td_center),
                Paragraph(str(d['roll_number']), td_center),
                Paragraph(f"<b>{d['enrollment_no']}</b>", td_bold_center),
                Paragraph(d['full_name'], td_left),
                Paragraph(d['division'], td_center),
                Paragraph(f"<b>{d['overall_percentage']}%</b>", td_danger),
                Paragraph(shortage_txt, td_left),
                Paragraph(f"{d['total_attended']} / {d['total_conducted']}", td_center)
            ])

        t_defaulters = Table(table_rows, colWidths=[28, 45, 95, 120, 32, 55, 105, 55])
        t_defaulters.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), maroon_color),
            ('GRID', (0, 0), (-1, -1), 0.5, border_color),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, light_bg]),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ]))
        story.append(t_defaulters)

    story.append(Spacer(1, 24))

    # 4. Official 3-Column Verification Footer
    # Col 1: College Stamp (width 160)
    # Col 2: Verification Note & Date (width 215)
    # Col 3: Principal Signature (width 160)
    stamp_flowable = None
    if college and college.college_stamp and college.college_stamp != 'default-stamp.png':
        cand = os.path.join(uploads_dir, college.college_stamp)
        if os.path.exists(cand):
            try:
                stamp_flowable = RLImage(cand, width=64, height=64)
                stamp_flowable.hAlign = 'CENTER'
            except Exception:
                stamp_flowable = None

    sig_flowable = None
    if college and college.principal_signature and college.principal_signature != 'default-signature.png':
        cand = os.path.join(uploads_dir, college.principal_signature)
        if os.path.exists(cand):
            try:
                sig_flowable = RLImage(cand, width=95, height=36)
                sig_flowable.hAlign = 'CENTER'
            except Exception:
                sig_flowable = None

    princ_name = (college.principal_name if (college and college.principal_name) else "Principal").strip()
    today_date_str = datetime.now().strftime('%d-%m-%Y')

    # Left cell: College Stamp
    left_cell_items = []
    if stamp_flowable:
        left_cell_items.append(stamp_flowable)
        left_cell_items.append(Spacer(1, 3))
    else:
        left_cell_items.append(Spacer(1, 40))
    left_cell_items.append(Paragraph("College Official Seal", sig_title))
    left_cell_items.append(Paragraph(f"Date: {today_date_str}", sig_sub))

    # Center cell: System generated watermark / notice
    center_cell_items = [
        Spacer(1, 36),
        Paragraph("Verified & System Generated Document", sig_title),
        Paragraph("CampusSync ERP &bull; Academic & Attendance Cell", sig_sub)
    ]

    # Right cell: Principal Signature
    right_cell_items = []
    if sig_flowable:
        right_cell_items.append(sig_flowable)
        right_cell_items.append(Spacer(1, 3))
    else:
        right_cell_items.append(Spacer(1, 40))
    right_cell_items.append(Paragraph("Principal Signature", sig_title))
    right_cell_items.append(Paragraph(f"<b>{princ_name}</b>", sig_name))
    right_cell_items.append(Paragraph("Principal / Head of Institution", sig_sub))

    footer_table = Table([[left_cell_items, center_cell_items, right_cell_items]], colWidths=[160, 215, 160])
    footer_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(footer_table)

    doc.build(story)
    buffer.seek(0)
    return buffer







# ==============================================================================
# QR CODE ATTENDANCE ENGINE (BACKEND LOGIC & SECURITY ENFORCEMENT)
# ==============================================================================
import math
import secrets
from datetime import datetime, timedelta

def calculate_haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculates great-circle distance between two GPS coordinates on Earth in meters.
    """
    try:
        lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)
    except (ValueError, TypeError):
        return 999999.0

    R = 6371000.0  # Earth's mean radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    return R * c


def start_faculty_qr_session(faculty_id, subject_id, division, semester, academic_year, lecture_date, lecture_no, session_type='Lecture', start_time=None, end_time=None):
    """
    Initializes or activates a live QR Attendance session for a specific lecture.
    Generates a secure QR session token and marks is_qr_active = 1.
    All exceptions handled cleanly with professional English feedback.
    """
    from models import LectureAttendanceSession, Subject, CollegeSetting, db
    import secrets
    from datetime import datetime, timedelta

    try:
        try:
            subject_id = int(subject_id)
            semester = int(semester)
        except (ValueError, TypeError):
            return {"success": False, "message": "Invalid subject ID or semester parameter. Numbers required."}

        division = str(division).strip().upper() if division else ""
        if not division:
            return {"success": False, "message": "Division parameter is required to start attendance session."}

        # Timetable Conflict & College Hours Validation for QR Session
        import re
        if not start_time or not end_time:
            m = re.search(r'\(([\d:]+)\s*-\s*([\d:]+)\)', lecture_no or '')
            if m:
                start_time, end_time = m.group(1), m.group(2)
            m_type = re.match(r'(Lecture|Lab)', lecture_no or '')
            if m_type:
                session_type = m_type.group(1)

        if start_time and end_time:
            is_valid, val_err = validate_session_time_and_conflict(
                semester=semester,
                division=division,
                lecture_date=lecture_date,
                start_time=start_time,
                end_time=end_time,
                subject_id=subject_id
            )
            if not is_valid:
                return {"success": False, "message": val_err}
            lecture_no = f"{session_type} ({start_time} - {end_time})"

        # Convert lecture_date if needed
        parsed_date = lecture_date
        if isinstance(lecture_date, str):
            try:
                parsed_date = datetime.strptime(lecture_date.strip(), "%Y-%m-%d").date()
            except (ValueError, TypeError):
                parsed_date = datetime.utcnow().date()

        sess = LectureAttendanceSession.query.filter_by(
            subject_id=subject_id,
            semester=semester,
            division=division,
            academic_year=academic_year,
            lecture_date=parsed_date,
            lecture_no=lecture_no
        ).first()

        if sess and sess.status == 'Submitted':
            return {
                "success": False,
                "message": "This lecture attendance has already been Final Submitted and Locked. QR sessions cannot be generated for finalized lectures."
            }

        submitted_check = LectureAttendanceSession.query.filter_by(
            subject_id=subject_id,
            semester=semester,
            division=division,
            academic_year=academic_year,
            lecture_date=parsed_date,
            lecture_no=lecture_no,
            status='Submitted'
        ).first()
        if submitted_check:
            return {
                "success": False,
                "message": "This lecture attendance has already been Final Submitted and Locked. QR sessions cannot be generated for finalized lectures."
            }

        if not sess:
            sess = LectureAttendanceSession(
                faculty_id=faculty_id,
                subject_id=subject_id,
                semester=semester,
                division=division,
                academic_year=academic_year,
                lecture_date=parsed_date,
                lecture_no=lecture_no,
                session_type=session_type,
                start_time=start_time,
                end_time=end_time,
                status='Draft',
                attendance_mode='QR',
                is_qr_active=1
            )
            db.session.add(sess)
        else:
            sess.faculty_id = faculty_id
            sess.session_type = session_type
            sess.start_time = start_time
            sess.end_time = end_time
            sess.lecture_no = lecture_no
            sess.attendance_mode = 'QR'
            sess.is_qr_active = 1

        sub = Subject.query.get(subject_id)
        sub_code = sub.subject_code if sub else "CS"
        rand_salt = secrets.token_hex(3).upper()

        sess.qr_session_token = f"CS-{sub_code}-{division}-{rand_salt}"
        sess.qr_session_expires_at = datetime.utcnow() + timedelta(minutes=10)

        db.session.commit()

        college = CollegeSetting.query.first()
        campus_lat = float(college.campus_latitude) if college and college.campus_latitude else 24.15953750
        campus_lng = float(college.campus_longitude) if college and college.campus_longitude else 72.40295313
        campus_radius = int(college.campus_radius_meters) if college and college.campus_radius_meters else 800

        rem_secs = 600
        if sess.qr_session_expires_at:
            rem_secs = max(0, int((sess.qr_session_expires_at - datetime.utcnow()).total_seconds()))

        return {
            "success": True,
            "session_id": sess.id,
            "token": sess.qr_session_token,
            "subject_code": sub_code,
            "subject_name": sub.subject_name if sub else "",
            "semester": sess.semester,
            "division": sess.division,
            "lecture_date": str(sess.lecture_date),
            "lecture_no": sess.lecture_no,
            "campus_lat": campus_lat,
            "campus_lng": campus_lng,
            "campus_radius": campus_radius,
            "duration_seconds": 600,
            "remaining_seconds": rem_secs,
            "expires_at": sess.qr_session_expires_at.isoformat() if sess.qr_session_expires_at else None
        }
    except Exception as e:
        db.session.rollback()
        return {"success": False, "message": f"Server error starting QR session: {str(e)}"}


def get_faculty_qr_live_roster(session_id):
    """
    Returns real-time status of enrolled students, scan timestamps, and pending proxy alerts.
    All database operations protected with comprehensive exception handling.
    """
    from models import LectureAttendanceSession, LectureAttendanceStudent, Student, AttendanceSecurityAlert, db

    try:
        if not session_id:
            return {"success": False, "message": "Missing session ID parameter."}

        sess = LectureAttendanceSession.query.get(session_id)
        if not sess:
            return {"success": False, "message": "Attendance session not found."}

        enrolled_students = Student.query.filter_by(
            semester=sess.semester,
            division=sess.division,
            status='Active'
        ).order_by(db.cast(Student.roll_number, db.Integer).asc()).all()

        entries = {e.student_id: e for e in LectureAttendanceStudent.query.filter_by(session_id=sess.id).all()}

        roster_data = []
        present_count = 0

        for st in enrolled_students:
            entry = entries.get(st.id)
            is_pres = (entry is not None and entry.status == 'Present')
            if is_pres:
                present_count += 1

            scanned_time_str = entry.scanned_at.strftime("%I:%M:%S %p") if (entry and entry.scanned_at) else "—"
            roster_data.append({
                "student_id": st.id,
                "roll_number": st.roll_number,
                "full_name": st.full_name,
                "status": "Present" if is_pres else "Absent",
                "scanned_at": scanned_time_str,
                "device_fingerprint": entry.device_fingerprint if entry else None,
                "distance_meters": round(entry.distance_meters, 1) if (entry and entry.distance_meters) else None,
                "is_verified": bool(entry.is_verified) if entry else False
            })

        alerts = AttendanceSecurityAlert.query.filter_by(session_id=sess.id, faculty_action='PENDING').order_by(AttendanceSecurityAlert.created_at.desc()).all()
        alerts_data = []
        for a in alerts:
            st = Student.query.get(a.student_id)
            alerts_data.append({
                "alert_id": a.id,
                "student_id": a.student_id,
                "roll_number": a.attempted_roll or (st.roll_number if st else ""),
                "student_name": st.full_name if st else "Unknown",
                "alert_type": a.alert_type,
                "message": a.alert_message,
                "created_at": a.created_at.strftime("%I:%M:%S %p") if a.created_at else ""
            })

        total_enrolled = len(enrolled_students)
        pct = round((present_count / total_enrolled * 100), 1) if total_enrolled > 0 else 0.0
        is_qr_live = bool(sess.is_qr_active) and (sess.status != 'Submitted')

        return {
            "success": True,
            "session_id": sess.id,
            "is_qr_active": is_qr_live,
            "is_locked": (sess.status == 'Submitted'),
            "status": sess.status,
            "present_count": present_count,
            "total_enrolled": total_enrolled,
            "remaining_count": max(0, total_enrolled - present_count),
            "percentage": pct,
            "attendance_percentage": pct,
            "roster": roster_data,
            "alerts": alerts_data,
            "pending_alerts": alerts_data,
            "pending_alerts_count": len(alerts_data)
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Database error fetching live roster: {str(e)}",
            "roster": [],
            "pending_alerts": []
        }


def resolve_faculty_security_alert(alert_id, faculty_id, action):
    """
    Faculty approves or rejects an anti-proxy security alert.
    All actions wrapped with robust exception handling and database rollback.
    """
    from models import AttendanceSecurityAlert, LectureAttendanceStudent, db
    from datetime import datetime

    try:
        alert = AttendanceSecurityAlert.query.get(alert_id)
        if not alert:
            return False, "Security alert record not found."

        action_norm = str(action).upper().strip()
        if action_norm not in ['APPROVED', 'REJECTED']:
            return False, "Invalid action. Decision must be APPROVED or REJECTED."

        alert.faculty_action = action_norm
        alert.resolved_by_faculty_id = faculty_id

        if action_norm == 'APPROVED':
            entry = LectureAttendanceStudent.query.filter_by(
                session_id=alert.session_id,
                student_id=alert.student_id
            ).first()
            if not entry:
                entry = LectureAttendanceStudent(
                    session_id=alert.session_id,
                    student_id=alert.student_id,
                    status='Present',
                    marked_method='QR',
                    scanned_at=datetime.utcnow(),
                    device_fingerprint=alert.device_fingerprint,
                    is_verified=1
                )
                db.session.add(entry)
            else:
                entry.status = 'Present'
                entry.is_verified = 1

            db.session.commit()
            if alert.session:
                _recalculate_cumulative_attendance(alert.session.subject_id, alert.session.semester, alert.session.division, alert.session.academic_year)
            return True, "Security alert approved. Attendance marked Present."
        elif action_norm == 'REJECTED':
            entry = LectureAttendanceStudent.query.filter_by(
                session_id=alert.session_id,
                student_id=alert.student_id
            ).first()
            if not entry:
                entry = LectureAttendanceStudent(
                    session_id=alert.session_id,
                    student_id=alert.student_id,
                    status='Absent',
                    marked_method='REJECTED_PROXY',
                    is_verified=0
                )
                db.session.add(entry)
            else:
                entry.status = 'Absent'
                entry.is_verified = 0
                entry.marked_method = 'REJECTED_PROXY'

            db.session.commit()
            if alert.session:
                _recalculate_cumulative_attendance(alert.session.subject_id, alert.session.semester, alert.session.division, alert.session.academic_year)
            return True, "Security alert rejected. Attendance recorded as Absent due to proxy violation."
    except Exception as e:
        db.session.rollback()
        return False, f"Database error resolving alert: {str(e)}"


def stop_faculty_qr_session(session_id):
    """
    Stops/closes the active QR attendance session.
    """
    from models import LectureAttendanceSession, db

    try:
        sess = LectureAttendanceSession.query.get(session_id)
        if not sess:
            return False, "Attendance session record not found."
        sess.is_qr_active = 0
        db.session.commit()
        return True, "QR Attendance session stopped successfully."
    except Exception as e:
        db.session.rollback()
        return False, f"Database error stopping session: {str(e)}"


def mark_student_qr_attendance_secure(student_id, session_id=None, token=None, lat=None, lng=None, device_fingerprint=None, device_model=None, request_approval=False, alert_id=None):
    """
    Enterprise-grade QR Attendance Validator:
    1. Validates active session (is_qr_active == 1) for student's Semester & Division.
    2. Validates session token integrity.
    3. Validates Campus GPS Geofence (Haversine distance <= campus_radius_meters).
    4. Enforces 1 Phone = 1 Student Device Binding with Faculty Approval fallback for borrowed devices.
    5. Flags Anti-Proxy conflict if device is used for multiple students in same session.
    6. Records attendance Present and recalculates summary statistics.
    All errors returned in clear, professional English.
    """
    from models import Student, LectureAttendanceSession, LectureAttendanceStudent, CollegeSetting, AttendanceSecurityAlert, db
    from datetime import datetime

    try:
        student = Student.query.get(student_id)
        if not student:
            return False, "Student account not found or session expired."

        sem = student.semester
        div = student.division

        # 1. Locate session first
        if session_id:
            sess = LectureAttendanceSession.query.get(session_id)
        else:
            sess = LectureAttendanceSession.query.filter_by(
                semester=sem,
                division=div,
                is_qr_active=1
            ).order_by(LectureAttendanceSession.id.desc()).first()

        if not sess or not sess.is_qr_active or sess.status == 'Submitted':
            return False, "This attendance session has been finalized and closed. No scans are accepted."

        # Verify class match
        if sess.semester != sem or sess.division != div:
            return False, f"Class Mismatch: This attendance session is for Semester {sess.semester} - Division {sess.division}. You are registered in Semester {sem} - Division {div}."

        # 2. Check if student already has a REJECTED or PENDING security alert
        prior_alert = AttendanceSecurityAlert.query.filter_by(
            session_id=sess.id,
            student_id=student.id
        ).order_by(AttendanceSecurityAlert.id.desc()).first()

        if prior_alert and prior_alert.faculty_action == 'REJECTED':
            return False, "Proxy Violation: Your attendance for this lecture was rejected by faculty. You are marked Absent. Re-scanning is prohibited."
        if prior_alert and prior_alert.faculty_action == 'PENDING' and prior_alert.alert_type in ['DUPLICATE_DEVICE', 'UNBOUND_DEVICE'] and request_approval:
            return False, "Your scan has already been submitted and is currently pending faculty security review."

        prior_entry = LectureAttendanceStudent.query.filter_by(
            session_id=sess.id,
            student_id=student.id
        ).first()
        if prior_entry and prior_entry.status == 'Absent' and prior_entry.marked_method == 'REJECTED_PROXY':
            return False, "Proxy Violation: Your attendance for this lecture was rejected by faculty. You are marked Absent. Re-scanning is prohibited."

        # Already marked Present check
        if prior_entry and prior_entry.status == 'Present':
            time_str = prior_entry.scanned_at.strftime("%I:%M:%S %p") if prior_entry.scanned_at else "earlier"
            return True, f"Attendance already verified: You have already been marked Present for this lecture session at {time_str}."

        # Enforce 10-minute session expiry
        if sess.qr_session_expires_at and datetime.utcnow() > sess.qr_session_expires_at:
            try:
                sess.is_qr_active = 0
                db.session.commit()
            except Exception:
                db.session.rollback()
            return False, "This 10-minute attendance window has expired. Please ask your faculty member to generate a new QR session."

        # 3. Token Validation
        if token and sess.qr_session_token:
            if token.strip().upper() != sess.qr_session_token.strip().upper():
                return False, "Invalid or expired QR Code token. Please scan the current live QR code displayed on the classroom screen."

        # 4. Campus Geofence Validation
        college = CollegeSetting.query.first()
        distance_meters = 0.0

        if college and college.campus_latitude and college.campus_longitude:
            if lat is None or lng is None:
                return False, {
                    "is_location_permission_error": True,
                    "message": "GPS location coordinates are required for campus geofence verification. Please enable location permissions on your device."
                }

            try:
                distance_meters = calculate_haversine_distance(
                    float(lat), float(lng),
                    float(college.campus_latitude), float(college.campus_longitude)
                )
            except (ValueError, TypeError):
                return False, {
                    "is_location_permission_error": True,
                    "message": "Invalid GPS coordinates received. Please enable location permissions and try scanning again."
                }

            max_allowed_radius = float(college.campus_radius_meters or 800)

            if distance_meters > max_allowed_radius:
                if distance_meters >= 1000:
                    dist_str = f"{distance_meters / 1000.0:.2f} km"
                else:
                    dist_str = f"{round(distance_meters)} meters"

                try:
                    alert = AttendanceSecurityAlert(
                        session_id=sess.id,
                        student_id=student.id,
                        attempted_roll=student.roll_number,
                        device_fingerprint=device_fingerprint,
                        alert_type='OUT_OF_GEOFENCE',
                        alert_message=f"Student scanned from {dist_str} away (Maximum allowed: {int(max_allowed_radius)}m).",
                        scan_latitude=lat,
                        scan_longitude=lng,
                        distance_meters=distance_meters,
                        faculty_action='PENDING'
                    )
                    db.session.add(alert)
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                return False, {
                    "is_distance_error": True,
                    "distance_str": dist_str,
                    "max_allowed": int(max_allowed_radius),
                    "message": f"Location verification failed: You are {dist_str} away from campus (Permitted classroom boundary: {int(max_allowed_radius)}m)."
                }

        # 5. Device Binding & Anti-Proxy Enforcement (1 Phone = 1 Student)
        if device_fingerprint:
            clean_dev = device_fingerprint.strip()

            # Anti-Duplication Check: Is this device ALREADY bound to another student?
            device_owner = Student.query.filter(
                Student.id != student.id,
                db.or_(
                    Student.device_fingerprint == clean_dev,
                    Student.device_fingerprint.like(f"{clean_dev}:::%"),
                    Student.device_fingerprint.like(f"%:::{clean_dev}")
                )
            ).first()

            if device_owner:
                msg = f"Proxy Attendance Blocked: This phone is registered to Roll #{device_owner.roll_number} ({device_owner.full_name}). Institutional policy permits only 1 student per physical phone."
                if not request_approval:
                    logged_alert_id = None
                    try:
                        alert = AttendanceSecurityAlert(
                            session_id=sess.id,
                            student_id=student.id,
                            attempted_roll=student.roll_number,
                            device_fingerprint=clean_dev,
                            conflicting_student_id=device_owner.id,
                            alert_type='DUPLICATE_DEVICE',
                            alert_message=f"Proxy Alert: Roll #{student.roll_number} ({student.full_name}) attempted to scan attendance from Roll #{device_owner.roll_number}'s ({device_owner.full_name}) phone. Warning modal displayed to student.",
                            scan_latitude=lat,
                            scan_longitude=lng,
                            distance_meters=distance_meters,
                            faculty_action='PENDING'
                        )
                        db.session.add(alert)
                        db.session.commit()
                        logged_alert_id = alert.id
                    except Exception:
                        db.session.rollback()

                    return False, {
                        "requires_approval": True,
                        "device_conflict": True,
                        "owner_roll": device_owner.roll_number,
                        "owner_name": device_owner.full_name,
                        "alert_id": logged_alert_id,
                        "message": msg
                    }
                else:
                    try:
                        alert = AttendanceSecurityAlert.query.get(alert_id) if alert_id else None
                        if not alert:
                            alert = AttendanceSecurityAlert(
                                session_id=sess.id,
                                student_id=student.id,
                                attempted_roll=student.roll_number,
                                device_fingerprint=clean_dev,
                                conflicting_student_id=device_owner.id,
                                alert_type='DUPLICATE_DEVICE',
                                scan_latitude=lat,
                                scan_longitude=lng,
                                distance_meters=distance_meters,
                                faculty_action='PENDING'
                            )
                            db.session.add(alert)
                        alert.alert_message = f"Attendance Approval Request: Roll #{student.roll_number} ({student.full_name}) confirmed attendance submission from Roll #{device_owner.roll_number}'s ({device_owner.full_name}) phone."
                        alert.faculty_action = 'PENDING'
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    return True, {
                        "pending_approval": True,
                        "message": "Attendance request submitted. Awaiting faculty approval."
                    }

            # Device Binding for student's own new device
            if not student.device_fingerprint:
                if not request_approval:
                    try:
                        student.device_fingerprint = clean_dev
                        student.device_model = device_model or "Mobile Device"
                        student.device_bound_at = datetime.utcnow()
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
            elif clean_dev not in student.device_fingerprint and not getattr(student, 'device_reset_allowed', 0):
                if not request_approval:
                    logged_alert_id = None
                    try:
                        alert = AttendanceSecurityAlert(
                            session_id=sess.id,
                            student_id=student.id,
                            attempted_roll=student.roll_number,
                            device_fingerprint=clean_dev,
                            alert_type='UNBOUND_DEVICE',
                            alert_message=f"Unregistered Device Alert: Roll #{student.roll_number} ({student.full_name}) scanned from unrecognized device (#{clean_dev[:8]}). Warning modal displayed to student.",
                            scan_latitude=lat,
                            scan_longitude=lng,
                            distance_meters=distance_meters,
                            faculty_action='PENDING'
                        )
                        db.session.add(alert)
                        db.session.commit()
                        logged_alert_id = alert.id
                    except Exception:
                        db.session.rollback()

                    return False, {
                        "requires_approval": True,
                        "device_mismatch": True,
                        "alert_id": logged_alert_id,
                        "message": "This phone does not match your registered personal device. Do you wish to submit an attendance request for faculty approval?"
                    }
                else:
                    try:
                        alert = AttendanceSecurityAlert.query.get(alert_id) if alert_id else None
                        if not alert:
                            alert = AttendanceSecurityAlert(
                                session_id=sess.id,
                                student_id=student.id,
                                attempted_roll=student.roll_number,
                                device_fingerprint=clean_dev,
                                alert_type='UNBOUND_DEVICE',
                                faculty_action='PENDING'
                            )
                            db.session.add(alert)
                        alert.alert_message = f"Attendance Approval Request: Roll #{student.roll_number} ({student.full_name}) submitted attendance from unrecognized device (#{clean_dev[:8]}). Awaiting faculty approval."
                        alert.faculty_action = 'PENDING'
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    return True, {
                        "pending_approval": True,
                        "message": "Attendance request submitted. Awaiting faculty approval."
                    }
            elif ':::' not in student.device_fingerprint and not student.device_fingerprint.startswith('HW-'):
                # Self-upgrade legacy passkey registration to include hardware fingerprint
                try:
                    student.device_fingerprint = f"{clean_dev}:::{student.device_fingerprint}"
                    db.session.commit()
                except Exception:
                    db.session.rollback()

            # Anti-Proxy Check: Has this same device already scanned for another student in this session?
            conflict_entry = LectureAttendanceStudent.query.filter(
                LectureAttendanceStudent.session_id == sess.id,
                LectureAttendanceStudent.student_id != student.id,
                LectureAttendanceStudent.device_fingerprint == clean_dev,
                LectureAttendanceStudent.status == 'Present'
            ).first()

            if conflict_entry:
                conflict_student = Student.query.get(conflict_entry.student_id)
                c_name = conflict_student.full_name if conflict_student else "another student"
                c_roll = conflict_student.roll_number if conflict_student else ""

                msg = f"Proxy Attendance Blocked: This phone was already used by Roll #{c_roll} ({c_name}) in this lecture. 1 Phone = 1 Student policy strictly enforced."
                if not request_approval:
                    logged_alert_id = None
                    try:
                        alert = AttendanceSecurityAlert(
                            session_id=sess.id,
                            student_id=student.id,
                            attempted_roll=student.roll_number,
                            device_fingerprint=clean_dev,
                            conflicting_student_id=conflict_entry.student_id,
                            alert_type='DUPLICATE_DEVICE',
                            alert_message=f"Duplicate Device Proxy Alert: Roll #{student.roll_number} ({student.full_name}) attempted to scan with phone already used by Roll #{c_roll} ({c_name}) in this session. Warning modal displayed.",
                            scan_latitude=lat,
                            scan_longitude=lng,
                            distance_meters=distance_meters,
                            faculty_action='PENDING'
                        )
                        db.session.add(alert)
                        db.session.commit()
                        logged_alert_id = alert.id
                    except Exception:
                        db.session.rollback()

                    return False, {
                        "requires_approval": True,
                        "device_conflict": True,
                        "owner_roll": c_roll,
                        "owner_name": c_name,
                        "alert_id": logged_alert_id,
                        "message": msg
                    }
                else:
                    try:
                        alert = AttendanceSecurityAlert.query.get(alert_id) if alert_id else None
                        if not alert:
                            alert = AttendanceSecurityAlert(
                                session_id=sess.id,
                                student_id=student.id,
                                attempted_roll=student.roll_number,
                                device_fingerprint=clean_dev,
                                conflicting_student_id=conflict_entry.student_id,
                                alert_type='DUPLICATE_DEVICE',
                                faculty_action='PENDING'
                            )
                            db.session.add(alert)
                        alert.alert_message = f"Attendance Approval Request: Roll #{student.roll_number} ({student.full_name}) requested faculty approval using phone already used by Roll #{c_roll} ({c_name}) in this session."
                        alert.faculty_action = 'PENDING'
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    return True, {
                        "pending_approval": True,
                        "message": "Attendance request submitted. Awaiting faculty approval."
                    }

        # Fallback: If student explicitly submitted with request_approval=True (e.g. Passkey unverified or alternate device)
        if request_approval:
            try:
                alert = AttendanceSecurityAlert.query.get(alert_id) if alert_id else None
                if not alert:
                    alert = AttendanceSecurityAlert(
                        session_id=sess.id,
                        student_id=student.id,
                        attempted_roll=student.roll_number,
                        device_fingerprint=clean_dev if device_fingerprint else None,
                        alert_type='UNBOUND_DEVICE',
                        scan_latitude=lat,
                        scan_longitude=lng,
                        distance_meters=distance_meters,
                        faculty_action='PENDING'
                    )
                    db.session.add(alert)
                alert.alert_message = f"Attendance Approval Request: Roll #{student.roll_number} ({student.full_name}) requested manual verification and approval (Screen lock unverified or alternate device)."
                alert.faculty_action = 'PENDING'
                db.session.commit()
            except Exception:
                db.session.rollback()
            return True, {
                "pending_approval": True,
                "message": "Attendance request submitted. Awaiting faculty approval."
            }

        # 6. Record Attendance as Present
        st_entry = LectureAttendanceStudent.query.filter_by(
            session_id=sess.id,
            student_id=student.id
        ).first()

        if not st_entry:
            st_entry = LectureAttendanceStudent(
                session_id=sess.id,
                student_id=student.id,
                status='Present',
                marked_method='QR',
                scanned_at=datetime.utcnow(),
                device_fingerprint=device_fingerprint,
                scan_latitude=lat,
                scan_longitude=lng,
                distance_meters=distance_meters,
                is_verified=1
            )
            db.session.add(st_entry)
        else:
            st_entry.status = 'Present'
            st_entry.marked_method = 'QR'
            st_entry.scanned_at = datetime.utcnow()
            st_entry.device_fingerprint = device_fingerprint
            st_entry.scan_latitude = lat
            st_entry.scan_longitude = lng
            st_entry.distance_meters = distance_meters
            st_entry.is_verified = 1

        db.session.commit()

        # Recalculate cumulative attendance
        try:
            _recalculate_cumulative_attendance(sess.subject_id, sess.semester, sess.division, sess.academic_year)
        except Exception:
            pass

        sub_code = sess.subject.subject_code if sess.subject else "Lecture"
        time_str = datetime.utcnow().strftime("%I:%M:%S %p")
        return True, f"Attendance successfully marked Present for {sub_code} ({sess.lecture_no}) at {time_str}!"

    except Exception as e:
        db.session.rollback()
        return False, f"An unexpected error occurred while processing attendance: {str(e)}"


def extend_faculty_qr_session(session_id, minutes=2):
    """
    Extends an active or expired QR session by specified minutes (default 2 minutes).
    Protected with full exception handling.
    """
    from models import LectureAttendanceSession, db
    from datetime import datetime, timedelta

    try:
        if not session_id:
            return False, "Missing session ID parameter."

        try:
            minutes = max(1, min(60, int(minutes)))
        except (ValueError, TypeError):
            minutes = 2

        sess = LectureAttendanceSession.query.get(session_id)
        if not sess:
            return False, "Attendance session record not found."
        if sess.status == 'Submitted':
            return False, "This attendance session has already been finalized and locked. It cannot be extended."

        base_time = max(datetime.utcnow(), sess.qr_session_expires_at or datetime.utcnow())
        sess.qr_session_expires_at = base_time + timedelta(minutes=minutes)
        sess.is_qr_active = 1
        db.session.commit()

        rem_secs = max(0, int((sess.qr_session_expires_at - datetime.utcnow()).total_seconds()))
        return True, {
            "message": f"QR Session extended by {minutes} minutes.",
            "remaining_seconds": rem_secs,
            "is_qr_active": 1
        }
    except Exception as e:
        db.session.rollback()
        return False, f"Database error extending session: {str(e)}"

