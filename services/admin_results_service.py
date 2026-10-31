"""
CampusSync ERP - Admin Results & Internal Marks Service
========================================================
File: services/admin_results_service.py

Provides service layer methods for Admin to:
- Fetch subject-wise internal marks matrix across all students in a division.
- Retrieve detailed multi-subject result breakdown for individual students.
- Supply data structures formatted for web UI rendering and Excel/PDF exporters.
"""

import json
from models import (
    db, Student, Subject, InternalMark, Faculty, FacultySubjectAssignment, AcademicSetting
)
from services.subject_service import get_subject_component_config

def _parse_roll_num(val):
    """Helper to parse numerical roll number for accurate sorting."""
    if val is None:
        return 999999
    digits = "".join(c for c in str(val).strip() if c.isdigit())
    return int(digits) if digits else 999999


def is_mark_final(mark):
    """
    Checks if an InternalMark record has been final-saved by faculty.
    Draft-saved marks return False and are excluded from Admin final results.
    """
    if not mark:
        return False
    if not mark.component_data:
        return True  # Legacy mark without component_data is considered final
    try:
        cdata = json.loads(mark.component_data) if isinstance(mark.component_data, str) else mark.component_data
        if isinstance(cdata, dict):
            st = str(cdata.get('save_type') or cdata.get('status') or '').lower()
            if st == 'draft':
                return False
            if 'is_final' in cdata and not cdata['is_final']:
                return False
    except Exception:
        pass
    return True


def get_admin_subject_marks_matrix(subject_id, division="A", semester=None, academic_year="2026-27"):
    """
    Fetches internal marks matrix for all active students in a given subject, division, and semester.
    Also retrieves assigned faculty information and submission status metrics.
    
    Args:
        subject_id (int): ID of the subject
        division (str): Division letter (e.g., 'A', 'B')
        semester (int, optional): Semester number
        academic_year (str): Academic year string (e.g., '2026-27')
        
    Returns:
        tuple: (data_dict, error_string)
    """
    if not subject_id:
        return None, "Subject ID is required."

    subject = Subject.query.get(subject_id)
    if not subject:
        return None, "Subject not found."

    sem = semester if semester is not None else subject.semester
    division = (division or "A").strip().upper()
    ay = (academic_year or "2026-27").strip()

    # 1. Fetch Assigned Faculty Information
    assignment = FacultySubjectAssignment.query.filter_by(
        subject_id=subject.id,
        division=division,
        status='Active'
    ).first()

    faculty_info = {
        "assigned": False,
        "faculty_name": "Not Assigned",
        "faculty_code": "-",
        "email": "-"
    }
    if assignment and assignment.faculty:
        faculty_info = {
            "assigned": True,
            "faculty_name": assignment.faculty.full_name,
            "faculty_code": assignment.faculty.faculty_code,
            "email": assignment.faculty.email
        }

    # 2. Fetch Active Students in this Course, Semester, and Division
    students = Student.query.filter_by(
        course=subject.course,
        semester=sem,
        division=division,
        status='Active'
    ).all()

    student_list = []
    saved_count = 0
    partially_entered_count = 0
    not_entered_count = 0

    latest_update = None

    for st in students:
        mark = InternalMark.query.filter_by(
            student_id=st.id,
            subject_id=subject.id,
            semester=sem,
            academic_year=ay
        ).first()

        status = "Not Entered"
        breakdown = None

        mark_is_final = is_mark_final(mark)

        if mark and mark_is_final:
            if latest_update is None or (mark.updated_at and mark.updated_at > latest_update):
                latest_update = mark.updated_at

            breakdown = {
                "id": mark.id,
                "marks_obtained": mark.marks_obtained,
                "max_marks": mark.max_marks,
                "test1": mark.test1,
                "test2": mark.test2,
                "test3": mark.test3,
                "internal_exam": mark.internal_exam,
                "active_learning": mark.active_learning,
                "class_assignment": mark.class_assignment,
                "home_assignment": mark.home_assignment,
                "attendance": mark.attendance,
                "practical_eval": mark.practical_eval,
                "viva": mark.viva,
                "journal": mark.journal,
                "component_data": json.loads(mark.component_data) if mark.component_data else None
            }

            if subject.subject_type == 'Theory':
                theory_vals = [mark.test1, mark.test2, mark.test3, mark.internal_exam,
                               mark.active_learning, mark.class_assignment, mark.home_assignment, mark.attendance]
                non_nulls = [v for v in theory_vals if v is not None]
                if len(non_nulls) == len(theory_vals):
                    status = "Saved"
                    saved_count += 1
                elif len(non_nulls) > 0:
                    status = "Partially Entered"
                    partially_entered_count += 1
                else:
                    status = "Saved" if mark.marks_obtained is not None else "Not Entered"
                    if status == "Saved":
                        saved_count += 1
                    else:
                        not_entered_count += 1
            else:
                prac_vals = [mark.internal_exam, mark.practical_eval, mark.journal]
                non_nulls = [v for v in prac_vals if v is not None]
                if len(non_nulls) == len(prac_vals):
                    status = "Saved"
                    saved_count += 1
                elif len(non_nulls) > 0:
                    status = "Partially Entered"
                    partially_entered_count += 1
                else:
                    status = "Saved" if mark.marks_obtained is not None else "Not Entered"
                    if status == "Saved":
                        saved_count += 1
                    else:
                        not_entered_count += 1
        else:
            not_entered_count += 1

        student_list.append({
            "id": st.id,
            "roll_number": st.roll_number,
            "enrollment_no": st.enrollment_no,
            "full_name": st.full_name,
            "division": st.division,
            "semester": st.semester,
            "marks_status": status if mark_is_final else "Not Entered",
            "marks_obtained": mark.marks_obtained if (mark and mark_is_final) else None,
            "max_marks": mark.max_marks if (mark and mark_is_final) else (subject.internal_marks or (50 if subject.subject_type == 'Theory' else 25)),
            "breakdown": breakdown if mark_is_final else None
        })

    # Sort students by roll number numerically ascending
    student_list.sort(key=lambda s: (_parse_roll_num(s.get("roll_number")), s.get("full_name", "").lower()))

    last_updated_str = latest_update.strftime("%d-%b-%Y %I:%M %p") if latest_update else "No records saved yet"

    from services.subject_service import get_subject_component_config
    comp_config = get_subject_component_config(subject)

    return {
        "subject": {
            "id": subject.id,
            "subject_code": subject.subject_code,
            "subject_name": subject.subject_name,
            "subject_type": subject.subject_type,
            "semester": subject.semester,
            "course": subject.course,
            "internal_marks": subject.internal_marks,
            "external_marks": subject.external_marks,
            "total_marks": subject.total_marks,
            "components": comp_config["components"] if comp_config else [],
            "maxes": comp_config["maxes"] if comp_config else {},
            "max_sum": comp_config["max_sum"] if comp_config else subject.internal_marks
        },
        "faculty": faculty_info,
        "division": division,
        "semester": sem,
        "academic_year": ay,
        "total_students": len(students),
        "saved_count": saved_count,
        "partially_entered_count": partially_entered_count,
        "not_entered_count": not_entered_count,
        "last_updated": last_updated_str,
        "students": student_list
    }, None


def get_admin_class_results_matrix(semester=1, division="A", academic_year="2026-27"):
    """
    Fetches multi-subject matrix for all active students in a division for a given semester and academic year.
    Returns:
        tuple: (data_dict, error_string)
    """
    division = (division or "A").strip().upper()
    ay = (academic_year or "2026-27").strip()
    sem = int(semester) if semester else 1

    # Fetch active students for this semester & division
    students = Student.query.filter(
        Student.semester == sem,
        Student.division == division,
        Student.status == 'Active'
    ).order_by(db.cast(Student.roll_number, db.Integer).asc(), Student.full_name.asc()).all()

    # Fetch active subjects for this semester
    subjects = Subject.query.filter_by(
        semester=sem,
        status='Active'
    ).order_by(Subject.subject_code.asc()).all()

    subject_list = []
    for sub in subjects:
        assign = FacultySubjectAssignment.query.filter_by(
            subject_id=sub.id,
            division=division,
            status='Active'
        ).first()
        faculty_name = assign.faculty.full_name if (assign and assign.faculty) else "Not Assigned"
        
        subject_list.append({
            "id": sub.id,
            "subject_code": sub.subject_code,
            "subject_name": sub.subject_name,
            "subject_type": sub.subject_type,
            "internal_marks": sub.internal_marks or (50 if sub.subject_type == 'Theory' else 25),
            "faculty_name": faculty_name
        })

    student_matrix = []
    for st in students:
        marks = InternalMark.query.filter_by(
            student_id=st.id,
            semester=sem,
            academic_year=ay
        ).all()
        mark_map = {m.subject_id: m for m in marks}

        subj_marks = {}
        total_obtained = 0.0
        total_max = 0.0
        has_any_mark = False

        for sub in subjects:
            m = mark_map.get(sub.id)
            max_m = sub.internal_marks or (50 if sub.subject_type == 'Theory' else 25)
            total_max += max_m
            
            if m and is_mark_final(m) and m.marks_obtained is not None:
                has_any_mark = True
                total_obtained += m.marks_obtained
                component_data = json.loads(m.component_data) if m.component_data else {}
                subj_marks[sub.id] = {
                    "obtained": m.marks_obtained,
                    "max": max_m,
                    "status": "Saved",
                    "breakdown": {
                        "test1": m.test1, "test2": m.test2, "test3": m.test3,
                        "internal_exam": m.internal_exam,
                        "active_learning": m.active_learning, "class_assignment": m.class_assignment,
                        "home_assignment": m.home_assignment, "attendance": m.attendance,
                        "practical_eval": m.practical_eval, "viva": m.viva, "journal": m.journal,
                        "component_data": component_data
                    }
                }
            else:
                subj_marks[sub.id] = {
                    "obtained": None,
                    "max": max_m,
                    "status": "Not Entered",
                    "breakdown": {}
                }

        percentage = round((total_obtained / total_max * 100), 2) if (total_max > 0 and has_any_mark) else None

        student_matrix.append({
            "id": st.id,
            "roll_number": st.roll_number,
            "enrollment_no": st.enrollment_no,
            "full_name": st.full_name,
            "division": st.division,
            "semester": st.semester,
            "subject_marks": subj_marks,
            "total_obtained": round(total_obtained, 2) if has_any_mark else None,
            "total_max": round(total_max, 2),
            "percentage": percentage
        })

    student_matrix.sort(key=lambda s: (_parse_roll_num(s.get("roll_number")), s.get("full_name", "").lower()))

    return {
        "semester": sem,
        "division": division,
        "academic_year": ay,
        "subjects": subject_list,
        "students": student_matrix
    }, None


def get_admin_student_full_result(student_id, semester=None, academic_year="2026-27"):
    """
    Fetches full student result breakdown across all subjects for a specified semester and academic year.
    Strictly partitions queries by student_id + semester + academic_year.
    
    Args:
        student_id (int): Student ID
        semester (int, optional): Semester number. Defaults to student's current semester.
        academic_year (str): Academic year. Defaults to '2026-27'.
        
    Returns:
        tuple: (data_dict, error_string)
    """
    student = Student.query.get(student_id)
    if not student:
        return None, "Student not found."

    from services.college_service import get_college_settings
    college = get_college_settings()

    target_sem = semester if semester is not None else (student.semester or 1)
    ay = (academic_year or "2026-27").strip()
    
    # Fetch active subjects for student's course & semester
    subjects = Subject.query.filter_by(
        course=student.course,
        semester=target_sem,
        status='Active'
    ).order_by(Subject.subject_code).all()

    # Query internal marks strictly filtered by student_id, semester AND academic_year
    marks_records = InternalMark.query.filter_by(
        student_id=student.id,
        semester=target_sem,
        academic_year=ay
    ).all()
    marks_by_subject = {m.subject_id: m for m in marks_records}

    results_data = []
    total_obtained = 0.0
    total_max = 0.0
    evaluated_count = 0
    passed_subjects = 0

    for sub in subjects:
        m = marks_by_subject.get(sub.id)
        max_marks = sub.internal_marks or (50 if sub.subject_type == 'Theory' else 25)
        breakdown = {}
        marks_obtained = None
        percentage = None
        is_pass = None

        if m and is_mark_final(m):
            marks_obtained = m.marks_obtained
            if marks_obtained is not None:
                total_obtained += marks_obtained
                evaluated_count += 1
                # Passing threshold: 40% of max internal marks
                passing_marks = max_marks * 0.40
                is_pass = (marks_obtained >= passing_marks)
                if is_pass:
                    passed_subjects += 1
            total_max += max_marks

            if m.component_data:
                try:
                    breakdown = json.loads(m.component_data)
                except Exception:
                    breakdown = {}
            if not breakdown:
                breakdown = {
                    "type": sub.subject_type,
                    "test1": m.test1, "test2": m.test2, "test3": m.test3,
                    "internal_exam": m.internal_exam,
                    "active_learning": m.active_learning, "class_assignment": m.class_assignment,
                    "home_assignment": m.home_assignment, "attendance": m.attendance,
                    "practical_eval": m.practical_eval, "viva": m.viva, "journal": m.journal,
                    "total": m.marks_obtained
                }
            if marks_obtained is not None and max_marks > 0:
                percentage = round((marks_obtained / max_marks) * 100, 1)
        else:
            total_max += max_marks

        comp_cfg = get_subject_component_config(sub)
        results_data.append({
            "subject_id": sub.id,
            "subject_code": sub.subject_code,
            "subject_name": sub.subject_name,
            "subject_type": sub.subject_type,
            "credits": sub.credits,
            "max_marks": max_marks,
            "marks_obtained": marks_obtained,
            "percentage": percentage,
            "is_pass": is_pass,
            "components": comp_cfg["components"] if comp_cfg else [],
            "maxes": comp_cfg["maxes"] if comp_cfg else {},
            "breakdown": breakdown
        })

    overall_percentage = round((total_obtained / total_max * 100), 2) if total_max > 0 else 0.0

    college_info = {
        "college_name": college.college_name if college else "CampusSync College",
        "college_short_name": college.college_short_name if college else "CampusSync",
        "college_type": college.college_type if college else "BCA",
        "logo": college.logo if college else "default-logo.png",
        "address": college.address if college else "",
        "city": college.city if college else "",
        "state": college.state if college else "",
        "pincode": college.pincode if college else "",
        "phone": college.phone if college else "",
        "email": college.email if college else "",
        "website": college.website if college else "",
        "principal_name": college.principal_name if (college and college.principal_name) else "Principal",
        "college_stamp": college.college_stamp if (college and college.college_stamp) else "default-stamp.png",
        "principal_signature": college.principal_signature if (college and college.principal_signature) else "default-signature.png"
    }

    from services.result_declaration_service import is_result_declared
    declared = is_result_declared(ay, target_sem, student.division or 'All')

    return {
        "college": college_info,
        "student": {
            "id": student.id,
            "roll_number": student.roll_number,
            "enrollment_no": student.enrollment_no,
            "full_name": student.full_name,
            "email": student.email,
            "course": student.course,
            "semester": target_sem,
            "division": student.division or "A",
            "profile_photo": student.profile_photo
        },
        "semester": target_sem,
        "academic_year": ay,
        "is_result_declared": declared,
        "total_subjects": len(subjects),
        "evaluated_count": evaluated_count,
        "passed_subjects": passed_subjects,
        "total_obtained": round(total_obtained, 2),
        "total_max": round(total_max, 2),
        "overall_percentage": overall_percentage,
        "results": results_data
    }, None
