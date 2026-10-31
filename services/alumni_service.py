"""
CampusSync ERP - Alumni / Old Student Service
============================================
File: services/alumni_service.py

Provides services for graduated / archived students:
- Lookup by Enrollment Number or Registered Email ID
- Secure, hashed 6-digit OTP generation, storage, and email dispatch
- OTP verification against expiry and secure hash
- Full semester-wise and consolidated marksheet data retrieval
- Single-semester and Consolidated (All Semesters) PDF marksheet generation
- Admin Old Students yearly report generation (Excel and PDF)
"""

import io
import os
import secrets
from datetime import datetime, timedelta
from flask import render_template, current_app
from flask_mail import Message
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db, mail
from models import ArchivedStudent, ArchivedInternalMark, ArchivedStudentOTP, Student, InternalMark
from services.college_service import get_college_settings

# OpenPyXL Imports
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ReportLab Imports
from reportlab.lib.pagesizes import A4, portrait, landscape
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, KeepTogether, PageBreak
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle


def mask_email(email_str):
    """
    Masks email for secure public display: e.g. devidparmar8954@gmail.com -> d***4@gmail.com
    """
    if not email_str or '@' not in email_str:
        return email_str or ""
    parts = email_str.split('@')
    name = parts[0]
    domain = parts[1]
    if len(name) <= 2:
        masked_name = name[0] + '***'
    else:
        masked_name = name[0] + '***' + name[-1]
    return f"{masked_name}@{domain}"


def find_old_student(identifier):
    """
    Finds a graduated/archived student by either:
    1. Enrollment Number (case-insensitive)
    2. Registered Email ID (case-insensitive)
    3. Roll Number (if unique)
    Also handles fallback to active Student if semester==6 and status=='Inactive'.
    """
    if not identifier:
        return None

    clean_id = str(identifier).strip()
    clean_lower = clean_id.lower()

    # 1. Search in ArchivedStudent
    student = ArchivedStudent.query.filter(
        (db.func.lower(ArchivedStudent.enrollment_no) == clean_lower) |
        (db.func.lower(ArchivedStudent.email) == clean_lower) |
        (ArchivedStudent.roll_number == clean_id)
    ).first()

    if student:
        return student

    # 2. Fallback: Check if student completed Sem 6 in active students table with status 'Inactive'
    active_st = Student.query.filter(
        (db.func.lower(Student.enrollment_no) == clean_lower) |
        (db.func.lower(Student.email) == clean_lower)
    ).first()

    if active_st and (active_st.semester == 6 or active_st.status != 'Active'):
        # Auto-mirror into ArchivedStudent if missing
        archived_st = ArchivedStudent(
            original_student_id=active_st.id,
            roll_number=active_st.roll_number,
            enrollment_no=active_st.enrollment_no,
            full_name=active_st.full_name,
            email=active_st.email,
            mobile=active_st.mobile,
            dob=active_st.dob,
            course=active_st.course or 'BCA',
            final_semester=active_st.semester,
            division=active_st.division,
            academic_year=active_st.academic_year or '2026-27',
            profile_photo=active_st.profile_photo or 'default-avatar.png',
            status='Archived'
        )
        try:
            db.session.add(archived_st)
            # Also copy marks to archived_internal_marks if missing
            marks = InternalMark.query.filter_by(student_id=active_st.id).all()
            for m in marks:
                existing_m = ArchivedInternalMark.query.filter_by(
                    enrollment_no=active_st.enrollment_no,
                    subject_id=m.subject_id,
                    semester=m.semester,
                    academic_year=m.academic_year
                ).first()
                if not existing_m:
                    archived_m = ArchivedInternalMark(
                        enrollment_no=active_st.enrollment_no,
                        student_name=active_st.full_name,
                        subject_id=m.subject_id,
                        subject_code=m.subject.subject_code if m.subject else None,
                        subject_name=m.subject.subject_name if m.subject else None,
                        semester=m.semester,
                        academic_year=m.academic_year,
                        marks_obtained=m.marks_obtained,
                        max_marks=m.max_marks,
                        component_data=m.component_data
                    )
                    db.session.add(archived_m)
            active_st.status = 'Inactive'
            db.session.commit()
            return archived_st
        except Exception as e:
            db.session.rollback()
            print(f"[Alumni Service] Auto-archive sync notice: {e}")

    return None


def generate_and_send_alumni_otp(student):
    """
    Generates a cryptographically random 6-digit OTP, securely hashes it before DB storage,
    and dispatches a branded HTML notification email to the student's registered email address.
    Returns:
        tuple: (success (bool), masked_email (str), error_message (str or None))
    """
    if not student or not getattr(student, 'email', None):
        return False, None, "Invalid student profile or missing registered email address."

    try:
        now = datetime.utcnow()

        # 0. Check if student is currently blocked
        active_block = ArchivedStudentOTP.query.filter(
            (ArchivedStudentOTP.enrollment_no == student.enrollment_no) |
            (ArchivedStudentOTP.email == student.email)
        ).filter(
            ArchivedStudentOTP.blocked_until > now
        ).first()
        if active_block and active_block.blocked_until:
            rem_sec = int((active_block.blocked_until - now).total_seconds())
            if rem_sec > 0:
                rem_hours = rem_sec // 3600
                rem_mins = (rem_sec % 3600) // 60
                return False, None, f"Maximum attempts exceeded (3). Your account is temporarily locked for 8 hours. Please try again after {rem_hours}h {rem_mins}m."

        # 1. Track attempt count within 8-hour window
        recent_cutoff = now - timedelta(hours=8)
        recent_otps = ArchivedStudentOTP.query.filter(
            (ArchivedStudentOTP.enrollment_no == student.enrollment_no) |
            (ArchivedStudentOTP.email == student.email),
            ArchivedStudentOTP.created_at >= recent_cutoff
        ).order_by(ArchivedStudentOTP.created_at.desc()).all()

        # If user has already requested OTP 3 times in 8 hours without success:
        if len(recent_otps) >= 3:
            block_until_time = now + timedelta(hours=8)
            for r in recent_otps:
                r.blocked_until = block_until_time
            db.session.commit()
            return False, None, "You have exceeded the maximum limit of 3 OTP requests. Your account has been temporarily locked for 8 hours for security reasons."

        current_attempt = len(recent_otps) + 1

        # 2. Generate 6-digit numeric OTP
        plain_otp = f"{secrets.randbelow(900000) + 100000}"

        # 3. Hash OTP using Werkzeug PBKDF2 / SHA256
        hashed_otp = generate_password_hash(plain_otp)

        # 4. Expire older unverified OTPs for this student (retaining records for attempt tracking)
        ArchivedStudentOTP.query.filter_by(
            enrollment_no=student.enrollment_no,
            is_verified=False
        ).update({'expires_at': now})

        # 5. Save new hashed OTP with 3-minute validity and verification attempt tracking
        expires_at = now + timedelta(minutes=3)
        otp_entry = ArchivedStudentOTP(
            enrollment_no=student.enrollment_no,
            email=student.email,
            otp_hash=hashed_otp,
            expires_at=expires_at,
            is_verified=False,
            attempts=0,
            blocked_until=None
        )
        db.session.add(otp_entry)
        db.session.commit()

        # 6. Fetch college settings
        college = get_college_settings()
        college_name = college.college_name if college else "CampusSync BCA College"

        # 7. Plain text and simple HTML email (No logo, plain text, OTP in bold on a new line)
        plain_text_body = (
            f"Dear {student.full_name},\n\n"
            f"Your One-Time Password (OTP) for verifying and accessing your CampusSync Old Student Results is:\n\n"
            f"{plain_otp}\n\n"
            f"This OTP is valid for 3 minutes. Please do not share this OTP with anyone.\n\n"
            f"Enrollment No: {student.enrollment_no}\n"
            f"Course: {student.course or 'BCA'}\n"
            f"Academic Year: {student.academic_year or 'Completed'}\n\n"
            f"Regards,\n"
            f"{college_name}\n"
        )

        html_body = render_template(
            'emails/alumni_otp.html',
            college=college,
            student_name=student.full_name,
            enrollment_no=student.enrollment_no,
            course=student.course or 'BCA',
            academic_year=student.academic_year or 'Completed',
            otp=plain_otp
        )

        subject = f"Result Verification OTP: {plain_otp} - {college_name}"
        sender = current_app.config.get('MAIL_DEFAULT_SENDER', 'devidparmar8954@gmail.com')

        from mail.brevo_service import is_brevo_configured, send_brevo_email
        if is_brevo_configured():
            b_success, b_info = send_brevo_email(
                to_email=student.email,
                subject=subject,
                html_content=html_body,
                to_name=student.full_name,
                text_content=plain_text_body,
                sender_email=sender,
                sender_name=college_name
            )
            if b_success:
                return True, mask_email(student.email), None
            else:
                current_app.logger.warning(f"Brevo Alumni OTP failed: {b_info}. Falling back to SMTP...")

        msg = Message(
            subject=subject,
            recipients=[student.email],
            body=plain_text_body,
            html=html_body,
            sender=sender
        )

        mail.send(msg)
        return True, mask_email(student.email), None

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"[Alumni OTP Error] {e}")
        return False, None, f"Failed to send OTP email: {str(e)}"


def verify_alumni_otp(enrollment_no, submitted_otp):
    """
    Verifies submitted OTP against the latest active hashed OTP record in the database.
    Checks:
    1. Account is not currently locked out (8-hour block)
    2. Record exists and not already verified
    3. Expiry timestamp (within 10 minutes)
    4. Hash comparison (tracks attempts, locks after 3 failures for 8 hours)
    Returns:
        tuple: (success (bool), error_message (str or None))
    """
    if not enrollment_no or not submitted_otp:
        return False, "Please enter the 6-digit OTP code."

    now = datetime.utcnow()
    clean_otp = str(submitted_otp).strip()

    # 1. Check if student account is currently blocked
    active_block = ArchivedStudentOTP.query.filter_by(enrollment_no=enrollment_no).filter(
        ArchivedStudentOTP.blocked_until > now
    ).first()
    if active_block and active_block.blocked_until:
        rem_sec = int((active_block.blocked_until - now).total_seconds())
        rem_hours = rem_sec // 3600
        rem_mins = (rem_sec % 3600) // 60
        return False, f"Account is locked for 8 hours due to exceeded attempts. Please try again after {rem_hours}h {rem_mins}m."

    # 2. Find the most recent active OTP for this student
    otp_record = ArchivedStudentOTP.query.filter_by(
        enrollment_no=enrollment_no,
        is_verified=False
    ).order_by(ArchivedStudentOTP.created_at.desc()).first()

    if not otp_record:
        return False, "No active OTP found. Please request a new code."

    # 3. Check expiration (3 minutes)
    if now > otp_record.expires_at:
        return False, "OTP has expired (3-minute limit exceeded). Please request a fresh OTP."

    # 4. Verify hash
    if not check_password_hash(otp_record.otp_hash, clean_otp):
        otp_record.attempts = (otp_record.attempts or 0) + 1
        if otp_record.attempts >= 3:
            otp_record.blocked_until = now + timedelta(hours=8)
            db.session.commit()
            return False, "Incorrect OTP entered 3 times. For security, your account has been locked for 8 hours."
        else:
            remaining = 3 - otp_record.attempts
            db.session.commit()
            return False, f"Invalid OTP code. You have {remaining} attempt(s) remaining before an 8-hour lockout."

    # 5. Success: mark verified and reset attempts
    try:
        otp_record.is_verified = True
        otp_record.attempts = 0
        otp_record.blocked_until = None
        db.session.commit()
        return True, None
    except Exception as e:
        db.session.rollback()
        return False, f"Verification database error: {str(e)}"



def get_archived_student_results(enrollment_no):
    """
    Fetches full marksheet and academic performance records for an archived student.
    Groups marks by Semester (1 to 6) and computes:
    - Subject details and component breakdowns
    - Semester total marks obtained and percentage
    - Overall consolidated total marks and graduation percentage
    - Class / Grade distinction
    Returns:
        tuple: (student_dict, semesters_data (dict), consolidated_summary (dict))
    """
    student = ArchivedStudent.query.filter_by(enrollment_no=enrollment_no).first()
    if not student:
        return None, {}, {}

    # Query all archived marks for this enrollment
    marks = ArchivedInternalMark.query.filter_by(enrollment_no=enrollment_no).order_by(
        ArchivedInternalMark.semester.asc(),
        ArchivedInternalMark.subject_code.asc()
    ).all()

    # Also check if any additional marks exist in internal_marks table
    if student.original_student_id:
        active_marks = InternalMark.query.filter_by(student_id=student.original_student_id).all()
        existing_keys = {(m.semester, m.subject_code or str(m.subject_id)) for m in marks}
        for am in active_marks:
            key = (am.semester, am.subject.subject_code if am.subject else str(am.subject_id))
            if key not in existing_keys:
                marks.append(ArchivedInternalMark(
                    enrollment_no=enrollment_no,
                    student_name=student.full_name,
                    subject_id=am.subject_id,
                    subject_code=am.subject.subject_code if am.subject else None,
                    subject_name=am.subject.subject_name if am.subject else None,
                    semester=am.semester,
                    academic_year=am.academic_year,
                    marks_obtained=am.marks_obtained,
                    max_marks=am.max_marks,
                    component_data=am.component_data
                ))

    # Group by semester
    semesters_data = {}
    for m in marks:
        sem = m.semester
        if sem not in semesters_data:
            semesters_data[sem] = {
                "semester": sem,
                "academic_year": m.academic_year,
                "subjects": [],
                "total_obtained": 0.0,
                "total_max": 0.0,
                "percentage": 0.0,
                "status": "PASS"
            }

        # Subject record
        sub_obt = float(m.marks_obtained or 0.0)
        sub_max = float(m.max_marks or 50)
        # Passing threshold is 35% of max marks
        sub_pass = sub_obt >= (0.35 * sub_max)
        if not sub_pass:
            semesters_data[sem]["status"] = "FAIL / ATKT"

        semesters_data[sem]["total_obtained"] += sub_obt
        semesters_data[sem]["total_max"] += sub_max
        semesters_data[sem]["subjects"].append({
            "subject_code": m.subject_code or "SUB",
            "subject_name": m.subject_name or "Subject",
            "marks_obtained": sub_obt,
            "max_marks": int(sub_max),
            "percentage": round((sub_obt / sub_max * 100), 2) if sub_max > 0 else 0,
            "status": "PASS" if sub_pass else "ATKT",
            "component_data": m.component_data
        })

    # Compute per-semester percentages
    total_all_obt = 0.0
    total_all_max = 0.0
    semesters_sorted = {}
    for sem in sorted(semesters_data.keys()):
        sd = semesters_data[sem]
        sd["total_obtained"] = round(sd["total_obtained"], 2)
        sd["total_max"] = round(sd["total_max"], 2)
        if sd["total_max"] > 0:
            sd["percentage"] = round((sd["total_obtained"] / sd["total_max"]) * 100, 2)
        else:
            sd["percentage"] = 0.0
        total_all_obt += sd["total_obtained"]
        total_all_max += sd["total_max"]
        semesters_sorted[sem] = sd

    # Overall Consolidated Summary
    overall_perc = round((total_all_obt / total_all_max * 100), 2) if total_all_max > 0 else 0.0
    
    # Class determination
    if overall_perc >= 70.0:
        division_class = "First Class with Distinction"
    elif overall_perc >= 60.0:
        division_class = "First Class"
    elif overall_perc >= 50.0:
        division_class = "Second Class"
    elif overall_perc >= 35.0:
        division_class = "Pass Class"
    else:
        division_class = "Fail"

    consolidated_summary = {
        "total_semesters": len(semesters_sorted),
        "available_semesters": list(semesters_sorted.keys()),
        "total_obtained": round(total_all_obt, 2),
        "total_max": round(total_all_max, 2),
        "overall_percentage": overall_perc,
        "class": division_class,
        "graduation_status": "COMPLETED" if all(sd["status"] == "PASS" for sd in semesters_sorted.values()) else "ATKT / PENDING"
    }

    return student.to_dict(), semesters_sorted, consolidated_summary


# ==============================================================================
# REPORTLAB PDF GENERATION FUNCTIONS (Single Semester & Consolidated All Sem)
# ==============================================================================

def generate_archived_student_semester_pdf(enrollment_no, semester):
    """
    Generates an official single-semester PDF marksheet for an archived student.
    Returns:
        io.BytesIO buffer containing the PDF file.
    """
    student_dict, semesters_data, _ = get_archived_student_results(enrollment_no)
    if not student_dict or int(semester) not in semesters_data:
        raise ValueError(f"No result records found for Semester {semester}")

    sem_info = semesters_data[int(semester)]
    college = get_college_settings()
    college_dict = college.to_dict() if college else {}

    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=portrait(A4),
        leftMargin=30,
        rightMargin=30,
        topMargin=25,
        bottomMargin=25
    )

    styles = getSampleStyleSheet()
    c_name = ParagraphStyle('ColName', fontName='Helvetica-Bold', fontSize=15, leading=18, textColor=colors.HexColor('#0F3460'), alignment=1, spaceAfter=2)
    c_sub = ParagraphStyle('ColSub', fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=colors.HexColor('#1E3A8A'), alignment=1, spaceAfter=6)
    section_title = ParagraphStyle('SecTitle', fontName='Helvetica-Bold', fontSize=10.5, leading=13, textColor=colors.HexColor('#0F2B5C'), spaceBefore=8, spaceAfter=5)
    info_lbl = ParagraphStyle('InfoLbl', fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=colors.HexColor('#334155'))
    info_val = ParagraphStyle('InfoVal', fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=colors.HexColor('#0F172A'))

    th_center = ParagraphStyle('THC', fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=colors.HexColor('#1E293B'), alignment=1)
    th_left = ParagraphStyle('THL', fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=colors.HexColor('#1E293B'), alignment=0)
    td_center = ParagraphStyle('TDC', fontName='Helvetica', fontSize=8.5, leading=11, textColor=colors.HexColor('#334155'), alignment=1)
    td_bold_center = ParagraphStyle('TDBC', fontName='Helvetica-Bold', fontSize=8.5, leading=11, textColor=colors.HexColor('#0F172A'), alignment=1)
    td_left = ParagraphStyle('TDL', fontName='Helvetica', fontSize=8.5, leading=11, textColor=colors.HexColor('#334155'), alignment=0)

    perc_lbl = ParagraphStyle('PercLbl', fontName='Helvetica-Bold', fontSize=9, leading=11, textColor=colors.HexColor('#0284C7'), alignment=1)
    perc_val = ParagraphStyle('PercVal', fontName='Helvetica-Bold', fontSize=17, leading=20, textColor=colors.HexColor('#0284C7'), alignment=1)

    story = []

    # 1. College Header
    college_title_text = (college_dict.get("college_name") or "BKM BCA COLLEGE PALANPUR").upper()
    story.append(Paragraph(college_title_text, c_name))
    story.append(Paragraph(f"OFFICIAL INTERNAL MARKSHEET — SEMESTER {semester}", c_sub))
    story.append(Spacer(1, 8))

    # 2. Student Info Box
    info_data = [
        [
            Paragraph("Student Name", info_lbl), Paragraph(f":  {student_dict.get('full_name', '-')}", info_val),
            Paragraph("Semester", info_lbl), Paragraph(f":  Semester {semester}", info_val)
        ],
        [
            Paragraph("Enrollment No", info_lbl), Paragraph(f":  {student_dict.get('enrollment_no', '-')}", info_val),
            Paragraph("Course", info_lbl), Paragraph(f":  {student_dict.get('course', 'BCA')}", info_val)
        ],
        [
            Paragraph("Roll No", info_lbl), Paragraph(f":  {student_dict.get('roll_number', '-')}", info_val),
            Paragraph("Academic Year", info_lbl), Paragraph(f":  {sem_info.get('academic_year', student_dict.get('academic_year', 'Historical'))}", info_val)
        ]
    ]
    info_table = Table(info_data, colWidths=[100, 185, 90, 160])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F8FAFC')),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#CBD5E1')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 10))

    # 3. Subject-wise Marks Table
    story.append(Paragraph(f"Semester {semester} Subject-wise Evaluation", section_title))
    headers = [
        Paragraph("Sr.", th_center),
        Paragraph("Subject Code", th_left),
        Paragraph("Subject Name", th_left),
        Paragraph("Internal Marks", th_center),
        Paragraph("Max Marks", th_center),
        Paragraph("Status", th_center)
    ]
    table_rows = [headers]

    for idx, sub in enumerate(sem_info["subjects"], start=1):
        m_obt = sub.get('marks_obtained', 0)
        m_obt_str = str(int(m_obt)) if float(m_obt).is_integer() else f"{m_obt:.2f}"
        m_max = sub.get('max_marks', 50)
        m_max_str = str(int(m_max)) if float(m_max).is_integer() else f"{m_max:.0f}"

        status_color = '#16a34a' if sub.get('status') == 'PASS' else '#dc2626'
        status_p = Paragraph(f"<font color='{status_color}'><b>{sub.get('status', 'PASS')}</b></font>", td_center)

        table_rows.append([
            Paragraph(str(idx), td_center),
            Paragraph(str(sub.get("subject_code", "-")), td_left),
            Paragraph(str(sub.get("subject_name", "-")), td_left),
            Paragraph(m_obt_str, td_bold_center),
            Paragraph(m_max_str, td_center),
            status_p
        ])

    sub_table = Table(table_rows, colWidths=[30, 85, 230, 75, 55, 60])
    t_style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#94A3B8')),
    ]
    for r_i in range(1, len(table_rows)):
        if r_i % 2 == 0:
            t_style.append(('BACKGROUND', (0, r_i), (-1, r_i), colors.HexColor('#F8FAFC')))
    sub_table.setStyle(TableStyle(t_style))
    story.append(sub_table)
    story.append(Spacer(1, 10))

    # 4. Semester Summary Box
    tot_sub = len(sem_info["subjects"])
    tot_obt = sem_info["total_obtained"]
    tot_obt_str = str(int(tot_obt)) if float(tot_obt).is_integer() else f"{tot_obt:.2f}"
    tot_max = sem_info["total_max"]
    tot_max_str = str(int(tot_max)) if float(tot_max).is_integer() else f"{tot_max:.0f}"
    perc = sem_info["percentage"]

    summary_left_data = [
        [Paragraph("Total Subjects", info_lbl), Paragraph(f":  {tot_sub}", info_val)],
        [Paragraph("Semester Marks Obtained", info_lbl), Paragraph(f":  {tot_obt_str}", info_val)],
        [Paragraph("Maximum Total Marks", info_lbl), Paragraph(f":  {tot_max_str}", info_val)],
        [Paragraph("Semester Result Status", info_lbl), Paragraph(f":  <b>{sem_info['status']}</b>", info_val)]
    ]
    summary_left_table = Table(summary_left_data, colWidths=[150, 170])
    summary_left_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]))

    perc_table = Table([
        [Paragraph("Semester Percentage", perc_lbl)],
        [Paragraph(f"{perc:.2f}%", perc_val)]
    ], colWidths=[175])
    perc_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F0F9FF')),
        ('BOX', (0, 0), (-1, -1), 1.5, colors.HexColor('#0284C7')),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))

    summary_row = Table([[summary_left_table, perc_table]], colWidths=[340, 195])
    summary_row.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    story.append(summary_row)
    story.append(Spacer(1, 25))

    # 5. Official Verification Stamp & Signature Footer
    sig_lbl = ParagraphStyle('SigLbl', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#475569'), alignment=1)
    sig_name = ParagraphStyle('SigName', fontName='Helvetica-Bold', fontSize=9, leading=11, textColor=colors.HexColor('#0F172A'), alignment=1)
    sig_sub = ParagraphStyle('SigSub', fontName='Helvetica', fontSize=7.5, leading=9, textColor=colors.HexColor('#64748B'), alignment=1)

    auth_table = Table([
        [
            Paragraph("VERIFIED BY<br/><br/><br/>_____________________<br/><b>Exam Committee Convener</b>", sig_lbl),
            Paragraph("COLLEGE SEAL<br/><br/>[ OFFICIAL SEAL ]<br/><br/>", sig_lbl),
            Paragraph("APPROVED BY<br/><br/><br/>_____________________<br/><b>Principal / Director</b>", sig_lbl)
        ]
    ], colWidths=[178, 178, 179])
    auth_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(KeepTogether(auth_table))

    doc.build(story)
    output.seek(0)
    return output


def generate_archived_student_consolidated_pdf(enrollment_no):
    """
    Generates a comprehensive Consolidated All-Semesters Marksheet PDF document
    containing all completed semesters for this archived student.
    Returns:
        io.BytesIO buffer.
    """
    student_dict, semesters_data, consolidated_summary = get_archived_student_results(enrollment_no)
    if not student_dict or not semesters_data:
        raise ValueError("No historical academic records found for this student.")

    college = get_college_settings()
    college_dict = college.to_dict() if college else {}

    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=portrait(A4),
        leftMargin=28,
        rightMargin=28,
        topMargin=22,
        bottomMargin=22
    )

    styles = getSampleStyleSheet()
    c_name = ParagraphStyle('CName', fontName='Helvetica-Bold', fontSize=15, leading=18, textColor=colors.HexColor('#0F3460'), alignment=1)
    c_sub = ParagraphStyle('CSub', fontName='Helvetica-Bold', fontSize=10.5, leading=13, textColor=colors.HexColor('#1E3A8A'), alignment=1, spaceAfter=6)
    section_title = ParagraphStyle('STitle', fontName='Helvetica-Bold', fontSize=10, leading=13, textColor=colors.HexColor('#0F2B5C'), spaceBefore=6, spaceAfter=4)
    info_lbl = ParagraphStyle('ILbl', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#334155'))
    info_val = ParagraphStyle('IVal', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#0F172A'))

    th_center = ParagraphStyle('THC', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#1E293B'), alignment=1)
    th_left = ParagraphStyle('THL', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#1E293B'), alignment=0)
    td_center = ParagraphStyle('TDC', fontName='Helvetica', fontSize=8, leading=10, textColor=colors.HexColor('#334155'), alignment=1)
    td_bold_center = ParagraphStyle('TDBC', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#0F172A'), alignment=1)
    td_left = ParagraphStyle('TDL', fontName='Helvetica', fontSize=8, leading=10, textColor=colors.HexColor('#334155'), alignment=0)

    story = []

    # 1. Header
    college_title_text = (college_dict.get("college_name") or "BKM BCA COLLEGE PALANPUR").upper()
    story.append(Paragraph(college_title_text, c_name))
    story.append(Paragraph("CONSOLIDATED HISTORICAL INTERNAL ASSESSMENT REPORT", c_sub))
    story.append(Spacer(1, 4))

    # 2. Student Info Header Box
    info_data = [
        [
            Paragraph("Student Name", info_lbl), Paragraph(f":  {student_dict.get('full_name', '-')}", info_val),
            Paragraph("Course", info_lbl), Paragraph(f":  {student_dict.get('course', 'BCA')} Program", info_val)
        ],
        [
            Paragraph("Enrollment No", info_lbl), Paragraph(f":  {student_dict.get('enrollment_no', '-')}", info_val),
            Paragraph("Academic Year", info_lbl), Paragraph(f":  {student_dict.get('academic_year', 'Graduated')}", info_val)
        ],
        [
            Paragraph("Roll No", info_lbl), Paragraph(f":  {student_dict.get('roll_number', '-')}", info_val),
            Paragraph("Graduation Status", info_lbl), Paragraph(f":  <b>{consolidated_summary.get('graduation_status', 'COMPLETED')}</b>", info_val)
        ]
    ]
    info_table = Table(info_data, colWidths=[90, 190, 95, 160])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#F8FAFC')),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#CBD5E1')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 8))

    # 3. Overall Consolidated Summary Banner Table
    overall_p = consolidated_summary.get('overall_percentage', 0.0)
    story.append(Paragraph("Consolidated Performance Summary Across All Semesters", section_title))

    summary_banner_data = [
        [
            Paragraph("<b>Total Semesters</b>", th_center),
            Paragraph("<b>Total Marks Obtained</b>", th_center),
            Paragraph("<b>Total Max Marks</b>", th_center),
            Paragraph("<b>Overall Percentage</b>", th_center),
            Paragraph("<b>Final Class Awarded</b>", th_center)
        ],
        [
            Paragraph(str(consolidated_summary.get('total_semesters', 0)), td_bold_center),
            Paragraph(f"{consolidated_summary.get('total_obtained', 0):.2f}", td_bold_center),
            Paragraph(f"{consolidated_summary.get('total_max', 0):.0f}", td_bold_center),
            Paragraph(f"<b>{overall_p:.2f}%</b>", td_bold_center),
            Paragraph(f"<b>{consolidated_summary.get('class', 'First Class')}</b>", td_bold_center)
        ]
    ]
    summary_banner = Table(summary_banner_data, colWidths=[80, 115, 105, 110, 125])
    summary_banner.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0F3460')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#EFF6FF')),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#93C5FD')),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#2563EB')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(summary_banner)
    story.append(Spacer(1, 10))

    # 4. Each Semester Marks Breakdown Tables
    for sem in sorted(semesters_data.keys()):
        sem_info = semesters_data[sem]
        sem_head = f"Semester {sem} Details ({sem_info.get('academic_year', 'Historical')}) — Total: {sem_info['total_obtained']}/{sem_info['total_max']} ({sem_info['percentage']}%) — [{sem_info['status']}]"
        story.append(Paragraph(sem_head, section_title))

        rows = [
            [
                Paragraph("Sr", th_center),
                Paragraph("Code", th_left),
                Paragraph("Subject Name", th_left),
                Paragraph("Obtained", th_center),
                Paragraph("Max", th_center),
                Paragraph("Status", th_center)
            ]
        ]
        for idx, sub in enumerate(sem_info["subjects"], start=1):
            obt_str = f"{sub['marks_obtained']:.2f}" if not float(sub['marks_obtained']).is_integer() else str(int(sub['marks_obtained']))
            max_str = str(int(sub['max_marks']))
            st_color = '#16a34a' if sub['status'] == 'PASS' else '#dc2626'
            rows.append([
                Paragraph(str(idx), td_center),
                Paragraph(str(sub['subject_code']), td_left),
                Paragraph(str(sub['subject_name']), td_left),
                Paragraph(obt_str, td_bold_center),
                Paragraph(max_str, td_center),
                Paragraph(f"<font color='{st_color}'><b>{sub['status']}</b></font>", td_center)
            ])

        sem_table = Table(rows, colWidths=[25, 65, 275, 55, 45, 70])
        sem_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#E2E8F0')),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(sem_table)
        story.append(Spacer(1, 6))

    # 5. Signatures Footer
    story.append(Spacer(1, 10))
    sig_lbl = ParagraphStyle('SigLbl', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#475569'), alignment=1)
    auth_table = Table([
        [
            Paragraph("VERIFIED BY<br/><br/><br/>_____________________<br/><b>Exam Committee Convener</b>", sig_lbl),
            Paragraph("COLLEGE SEAL<br/><br/>[ OFFICIAL SEAL ]<br/><br/>", sig_lbl),
            Paragraph("APPROVED BY<br/><br/><br/>_____________________<br/><b>Principal / Director</b>", sig_lbl)
        ]
    ], colWidths=[178, 178, 179])
    auth_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(KeepTogether(auth_table))

    doc.build(story)
    output.seek(0)
    return output


# ==============================================================================
# ADMIN OLD STUDENTS REPORT EXPORT FUNCTIONS (Excel & PDF)
# ==============================================================================

def export_archived_students_excel(academic_year=None):
    """
    Generates an Excel (.xlsx) report containing all graduated/archived students
    filtered strictly from the `archived_students` table for the specified academic year.
    Returns:
        io.BytesIO buffer.
    """
    college = get_college_settings()
    college_name = college.college_name if college else "CampusSync BCA College"

    query = ArchivedStudent.query
    if academic_year and academic_year.lower() != 'all':
        query = query.filter(ArchivedStudent.academic_year == academic_year)

    students = query.order_by(db.cast(ArchivedStudent.roll_number, db.Integer).asc()).all()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Old Students"

    # Styling definitions
    font_title = Font(name="Calibri", size=14, bold=True, color="FFFFFF")
    font_sub = Font(name="Calibri", size=11, bold=True, color="E0E7FF")
    font_header = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    font_row = Font(name="Calibri", size=10)
    font_bold_row = Font(name="Calibri", size=10, bold=True)

    fill_title = PatternFill(start_color="0F3460", end_color="0F3460", fill_type="solid")
    fill_header = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    fill_even = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    thin_border = Border(
        left=Side(style='thin', color='CBD5E1'),
        right=Side(style='thin', color='CBD5E1'),
        top=Side(style='thin', color='CBD5E1'),
        bottom=Side(style='thin', color='CBD5E1')
    )

    # 1. College Title Banner
    ws.merge_cells("A1:J1")
    title_cell = ws["A1"]
    title_cell.value = college_name.upper()
    title_cell.font = font_title
    title_cell.fill = fill_title
    title_cell.alignment = align_center
    ws.row_dimensions[1].height = 28

    # 2. Report Sub-banner
    year_str = f"Academic Year: {academic_year}" if academic_year and academic_year.lower() != 'all' else "All Graduated Batches"
    ws.merge_cells("A2:J2")
    sub_cell = ws["A2"]
    sub_cell.value = f"ARCHIVED / GRADUATED STUDENTS DIRECTORY ({year_str})"
    sub_cell.font = font_sub
    sub_cell.fill = fill_title
    sub_cell.alignment = align_center
    ws.row_dimensions[2].height = 20

    # 3. Column Headers
    headers = [
        "Sr No", "Roll No", "Enrollment No", "Full Name", "Email Address",
        "Mobile", "Course", "Final Semester", "Passing Year", "Archived Date"
    ]
    ws.append([]) # row 3 blank
    ws.row_dimensions[3].height = 8

    ws.append(headers) # row 4 headers
    ws.row_dimensions[4].height = 24

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=4, column=col_idx)
        cell.font = font_header
        cell.fill = fill_header
        cell.alignment = align_center
        cell.border = thin_border

    # 4. Data Rows
    for idx, st in enumerate(students, start=1):
        archived_date_str = st.archived_at.strftime("%d-%b-%Y") if st.archived_at else "-"
        row_data = [
            idx,
            st.roll_number,
            st.enrollment_no,
            st.full_name,
            st.email,
            st.mobile or "-",
            st.course or "BCA",
            f"Sem {st.final_semester}",
            st.academic_year or "-",
            archived_date_str
        ]
        ws.append(row_data)
        current_row = ws.max_row
        ws.row_dimensions[current_row].height = 20

        is_even = (idx % 2 == 0)
        for col_idx in range(1, len(row_data) + 1):
            cell = ws.cell(row=current_row, column=col_idx)
            cell.font = font_row
            cell.border = thin_border
            if is_even:
                cell.fill = fill_even

            if col_idx in (1, 2, 7, 8, 9, 10):
                cell.alignment = align_center
            elif col_idx in (3, 4, 5, 6):
                cell.alignment = align_left

    # Summary Row
    summary_row_num = ws.max_row + 1
    ws.cell(row=summary_row_num, column=1, value="")
    ws.merge_cells(start_row=summary_row_num, start_column=2, end_row=summary_row_num, end_column=4)
    sum_label = ws.cell(row=summary_row_num, column=2, value=f"Total Graduated Students: {len(students)}")
    sum_label.font = font_bold_row
    sum_label.alignment = align_left

    # Auto-adjust column widths
    for col in ws.columns:
        col_letter = get_column_letter(col[0].column)
        max_len = 0
        for cell in col:
            if cell.row > 2 and cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def export_archived_students_pdf(academic_year=None):
    """
    Generates an official institutional PDF directory report for graduated / archived students
    for the selected academic year using ReportLab.
    Returns:
        io.BytesIO buffer.
    """
    college = get_college_settings()
    college_name = college.college_name if college else "CampusSync BCA College"

    query = ArchivedStudent.query
    if academic_year and academic_year.lower() != 'all':
        query = query.filter(ArchivedStudent.academic_year == academic_year)

    students = query.order_by(db.cast(ArchivedStudent.roll_number, db.Integer).asc()).all()

    output = io.BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        leftMargin=25,
        rightMargin=25,
        topMargin=22,
        bottomMargin=22
    )

    styles = getSampleStyleSheet()
    c_name = ParagraphStyle('CName', fontName='Helvetica-Bold', fontSize=15, leading=18, textColor=colors.HexColor('#0F3460'), alignment=1)
    c_sub = ParagraphStyle('CSub', fontName='Helvetica-Bold', fontSize=10.5, leading=13, textColor=colors.HexColor('#1E3A8A'), alignment=1, spaceAfter=8)

    th_center = ParagraphStyle('THC', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#1E293B'), alignment=1)
    th_left = ParagraphStyle('THL', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#1E293B'), alignment=0)
    td_center = ParagraphStyle('TDC', fontName='Helvetica', fontSize=8, leading=10, textColor=colors.HexColor('#334155'), alignment=1)
    td_bold_center = ParagraphStyle('TDBC', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#0F172A'), alignment=1)
    td_left = ParagraphStyle('TDL', fontName='Helvetica', fontSize=8, leading=10, textColor=colors.HexColor('#334155'), alignment=0)

    story = []

    # 1. Header
    year_label = f"ACADEMIC YEAR: {academic_year}" if academic_year and academic_year.lower() != 'all' else "ALL GRADUATED BATCHES"
    story.append(Paragraph(college_name.upper(), c_name))
    story.append(Paragraph(f"GRADUATED / ARCHIVED STUDENTS DIRECTORY — {year_label}", c_sub))
    story.append(Spacer(1, 6))

    # 2. Table Data
    # Landscape A4 width is 842 pt. Margins = 50 pt total. Printable width = 792 pt.
    # col widths: 30, 45, 95, 170, 160, 85, 55, 65, 87 = 792
    headers = [
        Paragraph("Sr", th_center),
        Paragraph("Roll No", th_center),
        Paragraph("Enrollment No", th_left),
        Paragraph("Full Name", th_left),
        Paragraph("Email Address", th_left),
        Paragraph("Mobile", th_center),
        Paragraph("Course", th_center),
        Paragraph("Passing Year", th_center),
        Paragraph("Archived On", th_center)
    ]
    table_rows = [headers]

    for idx, st in enumerate(students, start=1):
        archived_date_str = st.archived_at.strftime("%d-%b-%Y") if st.archived_at else "-"
        table_rows.append([
            Paragraph(str(idx), td_center),
            Paragraph(str(st.roll_number), td_bold_center),
            Paragraph(str(st.enrollment_no), td_left),
            Paragraph(str(st.full_name), td_left),
            Paragraph(str(st.email), td_left),
            Paragraph(str(st.mobile or "-"), td_center),
            Paragraph(str(st.course or "BCA"), td_center),
            Paragraph(str(st.academic_year or "-"), td_center),
            Paragraph(archived_date_str, td_center)
        ])

    data_table = Table(table_rows, colWidths=[30, 45, 95, 170, 160, 85, 55, 65, 87])
    t_style = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#F1F5F9')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#CBD5E1')),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
    ]
    for r_i in range(1, len(table_rows)):
        if r_i % 2 == 0:
            t_style.append(('BACKGROUND', (0, r_i), (-1, r_i), colors.HexColor('#F8FAFC')))
    data_table.setStyle(TableStyle(t_style))
    story.append(data_table)
    story.append(Spacer(1, 12))

    # 3. Summary & Footer
    summary_p = Paragraph(f"<b>Total Graduated Students:</b> {len(students)} &nbsp;|&nbsp; <b>Report Generated:</b> {datetime.utcnow().strftime('%d-%b-%Y %H:%M UTC')}", td_left)
    story.append(summary_p)
    story.append(Spacer(1, 15))

    sig_lbl = ParagraphStyle('SigLbl', fontName='Helvetica-Bold', fontSize=8, leading=10, textColor=colors.HexColor('#475569'), alignment=1)
    auth_table = Table([
        [
            Paragraph("PREPARED BY<br/><br/><br/>_____________________<br/><b>System Administrator</b>", sig_lbl),
            Paragraph("COLLEGE SEAL<br/><br/>[ OFFICIAL SEAL ]<br/><br/>", sig_lbl),
            Paragraph("VERIFIED & APPROVED<br/><br/><br/>_____________________<br/><b>Principal / Director</b>", sig_lbl)
        ]
    ], colWidths=[264, 264, 264])
    auth_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
    ]))
    story.append(KeepTogether(auth_table))

    doc.build(story)
    output.seek(0)
    return output
