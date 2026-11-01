"""
CampusSync ERP - Assignment Service
===================================
File: services/assignment_service.py
Handles Assignment Submission tracking, student checklists, deadline status,
and automatic synchronization to Internal Assessment Marks.
"""

from datetime import datetime
import json
from extensions import db
from models import (
    Notification, Student, Subject, Faculty,
    AssignmentSubmission, InternalMark, AcademicSetting
)
from services.subject_service import get_subject_component_config
from services.academic_service import get_academic_settings
from services.academic_history_service import calculate_theory_internal_marks
from services.notification_service import get_ist_now


def get_faculty_assignment_notices(faculty_id, filter_by_cycle=True):
    """
    Fetches all notifications posted by a faculty member that have
    category == 'Assignment Submit Date'.
    Enriches with total target students count and submitted count.
    Strictly filters according to active semester cycle (Odd: 1, 3, 5 / Even: 2, 4, 6).
    """
    from services.academic_service import get_active_semesters
    active_sems = get_active_semesters()

    notices = Notification.query.filter_by(
        faculty_id=faculty_id,
        category='Assignment Submit Date'
    ).order_by(Notification.created_at.desc()).all()

    now = get_ist_now()
    result = []
    for n in notices:
        # Check active cycle filter
        if filter_by_cycle:
            if n.target_semester is not None and n.target_semester not in active_sems:
                continue
            if n.target_semester is None and n.subject and n.subject.semester not in active_sems:
                continue

        # Determine target student count
        stud_query = Student.query.filter_by(status='Active')
        if n.target_semester:
            stud_query = stud_query.filter_by(semester=n.target_semester)
        else:
            stud_query = stud_query.filter(Student.semester.in_(active_sems))
        if n.target_division and n.target_division != 'All':
            stud_query = stud_query.filter_by(division=n.target_division)
        total_students = stud_query.count()

        submitted_count = AssignmentSubmission.query.filter_by(
            notification_id=n.id,
            is_submitted=True
        ).count()

        is_expired = bool(n.end_date and now > n.end_date)
        is_final_saved = bool(n.is_final_saved)

        result.append({
            'notice': n,
            'total_students': total_students,
            'submitted_count': submitted_count,
            'pending_count': max(0, total_students - submitted_count),
            'is_expired': is_expired,
            'is_final_saved': is_final_saved,
            'final_saved_at': n.final_saved_at
        })

    return result


def get_assignment_submission_sheet(notification_id, faculty_id=None):
    """
    Retrieves full student verification sheet for a specific assignment notification.
    Returns:
        dict: {
            'notice': Notification,
            'subject': Subject,
            'max_assignment_marks': float,
            'students': [list of student objects with submission state],
            'total_students': int,
            'submitted_count': int,
            'pending_count': int,
            'is_expired': bool
        }
    """
    notice = Notification.query.get(notification_id)
    if not notice:
        return None

    # Check faculty ownership if faculty_id is passed
    if faculty_id and notice.faculty_id and notice.faculty_id != faculty_id:
        return None

    from services.academic_service import get_active_semesters
    active_sems = get_active_semesters()
    if notice.target_semester is not None and notice.target_semester not in active_sems:
        return None
    if notice.target_semester is None and notice.subject and notice.subject.semester not in active_sems:
        return None

    subject = notice.subject
    max_assignment_marks = 5.0

    if subject:
        cfg = get_subject_component_config(subject)
        if cfg and 'maxes' in cfg:
            if cfg.get('type') == 'Practical':
                max_assignment_marks = float(cfg['maxes'].get('journal', 5.0))
            else:
                max_assignment_marks = float(cfg['maxes'].get('home_assignment', 5.0))

    # Query active students belonging to target semester & division
    stud_query = Student.query.filter_by(status='Active')
    if notice.target_semester:
        stud_query = stud_query.filter_by(semester=notice.target_semester)
    else:
        stud_query = stud_query.filter(Student.semester.in_(active_sems))
    if notice.target_division and notice.target_division != 'All':
        stud_query = stud_query.filter_by(division=notice.target_division)

    students = stud_query.order_by(db.cast(Student.roll_number, db.Integer).asc()).all()

    # Query existing submissions for this assignment
    submissions = AssignmentSubmission.query.filter_by(notification_id=notice.id).all()
    sub_map = {s.student_id: s for s in submissions}

    now = get_ist_now()
    is_expired = bool(notice.end_date and now > notice.end_date)

    student_rows = []
    submitted_count = 0

    for st in students:
        sub = sub_map.get(st.id)
        is_sub = bool(sub and sub.is_submitted)
        if is_sub:
            submitted_count += 1

        marks = sub.marks_awarded if (sub and sub.marks_awarded is not None) else (max_assignment_marks if is_sub else 0.0)

        student_rows.append({
            'student_id': st.id,
            'roll_number': st.roll_number,
            'enrollment_no': st.enrollment_no,
            'full_name': st.full_name,
            'semester': st.semester,
            'division': st.division,
            'is_submitted': is_sub,
            'submitted_at': sub.submitted_at if sub else None,
            'marks_awarded': marks,
            'status': sub.status if sub else 'Pending',
            'remarks': sub.remarks if sub else ''
        })

    return {
        'notice': notice,
        'subject': subject,
        'max_assignment_marks': max_assignment_marks,
        'students': student_rows,
        'total_students': len(students),
        'submitted_count': submitted_count,
        'pending_count': len(students) - submitted_count,
        'is_expired': is_expired,
        'is_final_saved': bool(notice.is_final_saved),
        'final_saved_at': notice.final_saved_at
    }


def save_assignment_submissions(notification_id, faculty_id, submitted_student_ids, custom_marks_dict=None, sync_to_internal_marks=True):
    """
    Saves submitted students checkboxes and marks.
    If sync_to_internal_marks is True:
        Automatically updates 'home_assignment' (or 'journal' for Practical) in 'internal_marks'
        for each student and recalculates their total internal marks.
    Once Final Saved, the sheet is permanently locked and cannot be saved again.
    """
    notice = Notification.query.get(notification_id)
    if not notice:
        return False, "Assignment notice not found."

    # Prevent duplicate final saves
    if notice.is_final_saved:
        return False, f"This assignment was already Final Saved on {notice.final_saved_at.strftime('%d %b %Y, %I:%M %p') if notice.final_saved_at else ''} and is permanently locked."

    subject = notice.subject
    max_assignment_marks = 5.0

    if subject:
        cfg = get_subject_component_config(subject)
        if cfg and 'maxes' in cfg:
            if cfg.get('type') == 'Practical':
                max_assignment_marks = float(cfg['maxes'].get('journal', 5.0))
            else:
                max_assignment_marks = float(cfg['maxes'].get('home_assignment', 5.0))

    # Academic Year
    acad = get_academic_settings()
    academic_year = acad.academic_year if acad else '2026-27'

    # Fetch students for this assignment
    stud_query = Student.query.filter_by(status='Active')
    if notice.target_semester:
        stud_query = stud_query.filter_by(semester=notice.target_semester)
    if notice.target_division and notice.target_division != 'All':
        stud_query = stud_query.filter_by(division=notice.target_division)

    students = stud_query.all()
    submitted_set = set(int(sid) for sid in submitted_student_ids if str(sid).isdigit())
    now = get_ist_now()

    updated_count = 0

    for st in students:
        is_sub = st.id in submitted_set
        sub = AssignmentSubmission.query.filter_by(
            notification_id=notice.id,
            student_id=st.id
        ).first()

        if not sub:
            sub = AssignmentSubmission(
                notification_id=notice.id,
                student_id=st.id,
                subject_id=notice.subject_id or (subject.id if subject else 1),
                faculty_id=faculty_id
            )
            db.session.add(sub)

        # Determine marks
        if is_sub:
            if custom_marks_dict and str(st.id) in custom_marks_dict:
                try:
                    awarded = float(custom_marks_dict[str(st.id)])
                    awarded = min(max(awarded, 0.0), max_assignment_marks)
                except (ValueError, TypeError):
                    awarded = max_assignment_marks
            else:
                awarded = max_assignment_marks

            sub.is_submitted = True
            sub.status = 'Submitted'
            if not sub.submitted_at:
                sub.submitted_at = now
            sub.marks_awarded = awarded
            updated_count += 1
        else:
            sub.is_submitted = False
            sub.status = 'Pending'
            sub.submitted_at = None
            sub.marks_awarded = 0.0

        # Sync to Internal Assessment Marks
        if sync_to_internal_marks and notice.subject_id:
            sem = st.semester or notice.target_semester or 1
            im = InternalMark.query.filter_by(
                student_id=st.id,
                subject_id=notice.subject_id,
                semester=sem,
                academic_year=academic_year
            ).first()

            if not im:
                im = InternalMark(
                    student_id=st.id,
                    enrollment_no=st.enrollment_no,
                    subject_id=notice.subject_id,
                    semester=sem,
                    academic_year=academic_year,
                    marks_obtained=0.0,
                    max_marks=subject.internal_marks if subject else 50
                )
                db.session.add(im)

            subj_type = subject.subject_type if subject else 'Theory'

            if subj_type == 'Practical':
                im.journal = sub.marks_awarded if is_sub else 0.0
                # Recalculate practical internal
                pe = im.practical_eval or 0.0
                vi = im.viva or 0.0
                ie = im.internal_exam or 0.0
                jo = im.journal or 0.0
                im.marks_obtained = round(pe + vi + ie + jo, 2)
                im.component_data = json.dumps({
                    "type": "Practical",
                    "practical_eval": pe,
                    "viva": vi,
                    "internal_exam": ie,
                    "journal": jo,
                    "total": im.marks_obtained,
                    "max_marks": im.max_marks
                })
            else:
                # Theory subject: set home_assignment
                im.home_assignment = sub.marks_awarded if is_sub else 0.0
                t1 = im.test1 or 0.0
                t2 = im.test2 or 0.0
                t3 = im.test3 or 0.0
                ie = im.internal_exam or 0.0
                al = im.active_learning or 0.0
                ca = im.class_assignment or 0.0
                ha = im.home_assignment or 0.0
                att = im.attendance or 0.0

                calc = calculate_theory_internal_marks(
                    test1=t1, test2=t2, test3=t3, internal_exam=ie,
                    active_learning=al, class_assignment=ca, home_assignment=ha, attendance=att
                )
                im.marks_obtained = calc["total_internal"]
                im.component_data = json.dumps({
                    "type": "Theory",
                    "test1": t1, "test2": t2, "test3": t3,
                    "best2": calc["class_test_score"],
                    "internal_exam": ie,
                    "active_learning": al,
                    "class_assignment": ca,
                    "home_assignment": ha,
                    "attendance": att,
                    "total": calc["total_internal"],
                    "max_marks": im.max_marks
                })

    # Lock the assignment sheet permanently
    notice.is_final_saved = True
    notice.final_saved_at = now
    notice.final_saved_by = faculty_id

    db.session.commit()
    return True, f"Successfully Final Saved! {updated_count} students marked as submitted with full internal marks ({max_assignment_marks}/{max_assignment_marks}). This assignment is now locked."
