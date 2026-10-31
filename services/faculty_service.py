"""
CampusSync ERP - Faculty Service
================================
File: services/faculty_service.py
"""

import os
import json
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from extensions import db
from models import Faculty, FacultySubjectAssignment, Subject, Student, InternalMark


def get_faculty_statistics(filter_by_cycle=True):
    """
    Calculates dynamic Faculty statistics directly from the database.
    Returns:
        dict: { total, active, inactive, subjects }
    """
    total = Faculty.query.count()
    active = Faculty.query.filter_by(status='Active').count()
    inactive = Faculty.query.filter_by(status='Inactive').count()

    if filter_by_cycle:
        from services.academic_service import get_active_semesters
        active_sems = get_active_semesters()
        subjects_assigned = FacultySubjectAssignment.query.join(Subject).filter(
            FacultySubjectAssignment.status == 'Active',
            Subject.semester.in_(active_sems)
        ).count()
    else:
        subjects_assigned = FacultySubjectAssignment.query.filter_by(status='Active').count()

    return {
        "total": total,
        "active": active,
        "inactive": inactive,
        "subjects": subjects_assigned
    }


def get_all_faculty(search=None, status=None, department=None, filter_by_cycle=True):
    """
    Fetches faculty records from the database with optional search and filters.
    Groups assigned subjects and divisions for each faculty (scoped to active cycle).
    Returns:
        list[dict]: List of serialized faculty objects
    """
    query = Faculty.query

    # Search filter (Name, Code, Email, Department, Qualification)
    if search:
        search_term = f"%{search.strip()}%"
        query = query.filter(
            db.or_(
                Faculty.full_name.ilike(search_term),
                Faculty.faculty_code.ilike(search_term),
                Faculty.email.ilike(search_term),
                Faculty.department.ilike(search_term),
                Faculty.qualification.ilike(search_term)
            )
        )

    # Status filter
    if status and status.lower() != 'all':
        query = query.filter(Faculty.status == status.capitalize())

    # Department filter
    if department and department.lower() != 'all':
        query = query.filter(Faculty.department == department)

    faculty_records = query.order_by(Faculty.id.desc()).all()

    active_sems = None
    if filter_by_cycle:
        from services.academic_service import get_active_semesters
        active_sems = get_active_semesters()

    result = []
    for f in faculty_records:
        # Group assigned subjects by subject_id
        subject_map = {}
        for assign in f.assignments:
            if assign.status != 'Active':
                continue
            sub = assign.subject
            if not sub:
                continue
            if filter_by_cycle and active_sems and (sub.semester not in active_sems):
                continue
            if sub.subject_code not in subject_map:
                subject_map[sub.subject_code] = {
                    "code": sub.subject_code,
                    "name": sub.subject_name,
                    "semester": sub.semester,
                    "divisions": []
                }
            if assign.division and assign.division not in subject_map[sub.subject_code]["divisions"]:
                subject_map[sub.subject_code]["divisions"].append(assign.division)

        # Sort divisions alphabetically for each subject
        assigned_subjects_list = []
        for code, data in subject_map.items():
            data["divisions"].sort()
            assigned_subjects_list.append(data)

        # Build photo URL
        photo_url = None
        if f.profile_photo and f.profile_photo != 'default-avatar.png':
            photo_url = f"/uploads/faculty/{f.profile_photo}"

        result.append({
            "id": f.id,
            "code": f.faculty_code,
            "fullName": f.full_name,
            "email": f.email,
            "mobile": f.mobile,
            "gender": f.gender or "Male",
            "dob": str(f.dob) if f.dob else "",
            "qualification": f.qualification or "",
            "designation": f.designation or "",
            "department": f.department or "",
            "joiningDate": str(f.joining_date) if f.joining_date else "",
            "address": f.address or "",
            "city": f.city or "",
            "state": f.state or "",
            "pincode": f.pincode or "",
            "photo": photo_url,
            "status": f.status,
            "assignedSubjects": assigned_subjects_list
        })

    return result


def get_available_subjects(course=None):
    """
    Fetches all active subjects from the existing `subjects` table.
    Returns:
        list[dict]: Subjects grouped with code, name, semester, course
    """
    query = Subject.query.filter_by(status='Active')
    if course:
        query = query.filter_by(course=course)

    subjects = query.order_by(Subject.semester.asc(), Subject.subject_code.asc()).all()

    res = []
    for s in subjects:
        assigned_map = {}
        for a in s.faculty_assignments:
            if a.status == 'Active' and a.faculty:
                assigned_map[a.division] = {
                    "faculty_id": a.faculty_id,
                    "faculty_name": a.faculty.full_name,
                    "faculty_code": a.faculty.faculty_code
                }
        res.append({
            "id": s.id,
            "code": s.subject_code,
            "name": s.subject_name,
            "semester": s.semester,
            "course": s.course,
            "type": s.subject_type,
            "assigned_divisions": assigned_map
        })
    return res


def get_faculty_by_id(faculty_id):
    """
    Fetches a single faculty member by ID with grouped assigned subjects.
    Returns:
        dict or None
    """
    f = Faculty.query.get(faculty_id)
    if not f:
        return None

    subject_map = {}
    for assign in f.assignments:
        sub = assign.subject
        if not sub:
            continue
        if sub.subject_code not in subject_map:
            subject_map[sub.subject_code] = {
                "code": sub.subject_code,
                "name": sub.subject_name,
                "semester": sub.semester,
                "divisions": []
            }
        if assign.division and assign.division not in subject_map[sub.subject_code]["divisions"]:
            subject_map[sub.subject_code]["divisions"].append(assign.division)

    assigned_subjects_list = []
    for code, data in subject_map.items():
        data["divisions"].sort()
        assigned_subjects_list.append(data)

    photo_url = None
    if f.profile_photo and f.profile_photo != 'default-avatar.png':
        photo_url = f"/uploads/faculty/{f.profile_photo}"

    return {
        "id": f.id,
        "code": f.faculty_code,
        "fullName": f.full_name,
        "email": f.email,
        "mobile": f.mobile,
        "gender": f.gender or "Male",
        "dob": str(f.dob) if f.dob else "",
        "qualification": f.qualification or "",
        "designation": f.designation or "",
        "department": f.department or "",
        "joiningDate": str(f.joining_date) if f.joining_date else "",
        "address": f.address or "",
        "city": f.city or "",
        "state": f.state or "",
        "pincode": f.pincode or "",
        "photo": photo_url,
        "status": f.status,
        "assignedSubjects": assigned_subjects_list
    }


def _save_faculty_photo(photo_file, faculty_code):
    """
    Saves uploaded faculty photo to uploads/faculty/ directory.
    Returns filename or None.
    """
    if not photo_file or photo_file.filename == '':
        return None

    ext = os.path.splitext(photo_file.filename)[1].lower()
    allowed_exts = ['.jpg', '.jpeg', '.png', '.webp', '.gif']
    if ext not in allowed_exts:
        return None

    upload_folder = os.path.join(current_app.root_path, 'uploads', 'faculty')
    os.makedirs(upload_folder, exist_ok=True)

    timestamp = int(datetime.utcnow().timestamp())
    clean_code = "".join(c for c in faculty_code if c.isalnum() or c in ('_', '-'))
    filename = f"faculty_{clean_code}_{timestamp}{ext}"
    photo_file.save(os.path.join(upload_folder, filename))
    return filename


def create_faculty(form_data, photo_file=None):
    """
    Creates a new faculty record and associated subject assignments.
    Hashes default password (mobile number) using Werkzeug.
    Returns:
        tuple: (new_faculty, None) on success or (None, error_message) on failure
    """
    full_name = form_data.get('full_name', '').strip()
    faculty_code = form_data.get('faculty_code', '').strip().upper()
    email = form_data.get('email', '').strip().lower()
    mobile = form_data.get('mobile', '').strip()
    gender = form_data.get('gender', 'Male')
    dob_raw = form_data.get('dob', '').strip()
    qualification = form_data.get('qualification', '').strip()
    designation = form_data.get('designation', '').strip()
    department = form_data.get('department', '').strip()
    joining_date_raw = form_data.get('joining_date', '').strip()
    status = form_data.get('status', 'Active')
    address = form_data.get('address', '').strip()
    city = form_data.get('city', '').strip()
    state = form_data.get('state', '').strip()
    pincode = form_data.get('pincode', '').strip()

    # Validations
    if not full_name or not faculty_code or not email or not mobile or not designation or not department:
        return None, "Please fill in all required fields marked with an asterisk (*)."

    if not mobile.isdigit() or len(mobile) != 10:
        return None, "Please enter a valid 10-digit mobile number."

    if Faculty.query.filter_by(faculty_code=faculty_code).first():
        return None, f"Faculty code '{faculty_code}' already exists."

    if Faculty.query.filter_by(email=email).first():
        return None, f"Email '{email}' is already registered."

    # Parse dates
    dob = None
    if dob_raw:
        try:
            dob = datetime.strptime(dob_raw, '%Y-%m-%d').date()
        except ValueError:
            pass

    joining_date = None
    if joining_date_raw:
        try:
            joining_date = datetime.strptime(joining_date_raw, '%Y-%m-%d').date()
        except ValueError:
            pass

    # Save photo if uploaded
    photo_filename = 'default-avatar.png'
    if photo_file:
        saved = _save_faculty_photo(photo_file, faculty_code)
        if saved:
            photo_filename = saved

    # Hash default password (mobile)
    password_hash = generate_password_hash(mobile)

    try:
        new_faculty = Faculty(
            faculty_code=faculty_code,
            full_name=full_name,
            email=email,
            mobile=mobile,
            gender=gender,
            dob=dob,
            qualification=qualification,
            designation=designation,
            department=department,
            joining_date=joining_date,
            address=address,
            city=city,
            state=state,
            pincode=pincode,
            profile_photo=photo_filename,
            password=password_hash,
            password_changed=False,
            status=status
        )
        db.session.add(new_faculty)
        db.session.flush()  # Generates new_faculty.id

        # Process subject assignments
        assigned_subjects_raw = form_data.get('assigned_subjects', '[]')
        try:
            assigned_subjects = json.loads(assigned_subjects_raw) if isinstance(assigned_subjects_raw, str) else assigned_subjects_raw
        except Exception:
            assigned_subjects = []

        seen_assignments = set()
        for item in assigned_subjects:
            sub_code = item.get('code')
            divisions = item.get('divisions', ['A'])
            if not sub_code:
                continue

            sub = Subject.query.filter_by(subject_code=sub_code).first()
            if not sub:
                continue

            for div in divisions:
                div = div.strip().upper()
                if not div:
                    div = 'A'
                assign_key = (new_faculty.id, sub.id, div)
                if assign_key in seen_assignments:
                    continue
                seen_assignments.add(assign_key)

                # Prevent duplicate assignment in same semester and division
                existing_assignment = FacultySubjectAssignment.query.filter_by(
                    subject_id=sub.id,
                    division=div,
                    status='Active'
                ).first()
                if existing_assignment and existing_assignment.faculty:
                    other_fac = existing_assignment.faculty
                    db.session.rollback()
                    return None, f"Cannot assign '{sub.subject_code} - {sub.subject_name}' for Division '{div}': It is already assigned to faculty '{other_fac.full_name}' ({other_fac.faculty_code}). Two faculty members cannot teach the same subject in the same division."

                assignment = FacultySubjectAssignment(
                    faculty_id=new_faculty.id,
                    subject_id=sub.id,
                    division=div,
                    status='Active'
                )
                db.session.add(assignment)

        db.session.commit()
        return new_faculty, None

    except Exception as e:
        db.session.rollback()
        return None, f"Database error while creating faculty: {str(e)}"


def update_faculty(faculty_id, form_data, photo_file=None):
    """
    Updates existing faculty information and subject assignments.
    Preserves existing password unless explicitly reset.
    Returns:
        tuple: (faculty, None) on success or (None, error_message) on failure
    """
    faculty = Faculty.query.get(faculty_id)
    if not faculty:
        return None, "Faculty record not found."

    full_name = form_data.get('full_name', '').strip()
    faculty_code = form_data.get('faculty_code', '').strip().upper()
    email = form_data.get('email', '').strip().lower()
    mobile = form_data.get('mobile', '').strip()
    gender = form_data.get('gender', 'Male')
    dob_raw = form_data.get('dob', '').strip()
    qualification = form_data.get('qualification', '').strip()
    designation = form_data.get('designation', '').strip()
    department = form_data.get('department', '').strip()
    joining_date_raw = form_data.get('joining_date', '').strip()
    status = form_data.get('status', 'Active')
    address = form_data.get('address', '').strip()
    city = form_data.get('city', '').strip()
    state = form_data.get('state', '').strip()
    pincode = form_data.get('pincode', '').strip()

    if not full_name or not faculty_code or not email or not mobile or not designation or not department:
        return None, "Please fill in all required fields marked with an asterisk (*)."

    if not mobile.isdigit() or len(mobile) != 10:
        return None, "Please enter a valid 10-digit mobile number."

    # Check duplicate code
    dup_code = Faculty.query.filter(Faculty.faculty_code == faculty_code, Faculty.id != faculty_id).first()
    if dup_code:
        return None, f"Faculty code '{faculty_code}' is already assigned to another faculty."

    # Check duplicate email
    dup_email = Faculty.query.filter(Faculty.email == email, Faculty.id != faculty_id).first()
    if dup_email:
        return None, f"Email '{email}' is already registered with another faculty."

    # Parse dates
    dob = None
    if dob_raw:
        try:
            dob = datetime.strptime(dob_raw, '%Y-%m-%d').date()
        except ValueError:
            pass

    joining_date = None
    if joining_date_raw:
        try:
            joining_date = datetime.strptime(joining_date_raw, '%Y-%m-%d').date()
        except ValueError:
            pass

    try:
        faculty.full_name = full_name
        faculty.faculty_code = faculty_code
        faculty.email = email
        faculty.mobile = mobile
        faculty.gender = gender
        faculty.dob = dob
        faculty.qualification = qualification
        faculty.designation = designation
        faculty.department = department
        faculty.joining_date = joining_date
        faculty.status = status
        faculty.address = address
        faculty.city = city
        faculty.state = state
        faculty.pincode = pincode

        # Handle photo update
        if photo_file:
            saved = _save_faculty_photo(photo_file, faculty_code)
            if saved:
                faculty.profile_photo = saved

        # Remove existing assignments and rebuild
        FacultySubjectAssignment.query.filter_by(faculty_id=faculty.id).delete()

        assigned_subjects_raw = form_data.get('assigned_subjects', '[]')
        try:
            assigned_subjects = json.loads(assigned_subjects_raw) if isinstance(assigned_subjects_raw, str) else assigned_subjects_raw
        except Exception:
            assigned_subjects = []

        seen_assignments = set()
        for item in assigned_subjects:
            sub_code = item.get('code')
            divisions = item.get('divisions', ['A'])
            if not sub_code:
                continue

            sub = Subject.query.filter_by(subject_code=sub_code).first()
            if not sub:
                continue

            for div in divisions:
                div = div.strip().upper()
                if not div:
                    div = 'A'
                assign_key = (faculty.id, sub.id, div)
                if assign_key in seen_assignments:
                    continue
                seen_assignments.add(assign_key)

                # Prevent duplicate assignment in same semester and division
                existing_assignment = FacultySubjectAssignment.query.filter_by(
                    subject_id=sub.id,
                    division=div,
                    status='Active'
                ).filter(FacultySubjectAssignment.faculty_id != faculty.id).first()
                if existing_assignment and existing_assignment.faculty:
                    other_fac = existing_assignment.faculty
                    db.session.rollback()
                    return None, f"Cannot assign '{sub.subject_code} - {sub.subject_name}' for Division '{div}': It is already assigned to faculty '{other_fac.full_name}' ({other_fac.faculty_code}). Two faculty members cannot teach the same subject in the same division."

                assignment = FacultySubjectAssignment(
                    faculty_id=faculty.id,
                    subject_id=sub.id,
                    division=div,
                    status='Active'
                )
                db.session.add(assignment)

        db.session.commit()
        return faculty, None

    except Exception as e:
        db.session.rollback()
        return None, f"Database error while updating faculty: {str(e)}"


def delete_faculty(faculty_id):
    """
    Deletes a faculty member by ID.
    Foreign key cascading automatically removes associated assignments,
    leaving master subjects completely intact.
    Returns:
        tuple: (True, None) on success or (False, error_message) on failure
    """
    faculty = Faculty.query.get(faculty_id)
    if not faculty:
        return False, "Faculty member not found."

    try:
        db.session.delete(faculty)
        db.session.commit()
        return True, None
    except Exception as e:
        db.session.rollback()
        return False, f"Failed to delete faculty: {str(e)}"


# ==============================================================================
# FACULTY AUTHENTICATION, DASHBOARD & PORTAL SERVICES
# ==============================================================================

def get_faculty_model(faculty_id):
    """
    Fetches the Faculty ORM instance by ID.
    Returns:
        Faculty model or None
    """
    if not faculty_id:
        return None
    return Faculty.query.get(int(faculty_id))


def authenticate_faculty(email, password):
    """
    Handles Faculty Login authentication logic using SQLAlchemy ORM.
    1. Finds faculty by email.
    2. Verifies hashed password using check_password_hash() (with fallback).
    3. Checks if faculty status is Active (blocks if Inactive).
    4. Updates last_login timestamp upon successful authentication.
    Returns:
        tuple: (faculty, None) on success, or (None, error_message) on failure.
    """
    if not email or not password:
        return None, "Invalid email or password."

    email_clean = email.strip().lower()
    faculty = Faculty.query.filter(
        (Faculty.email == email_clean) | (Faculty.faculty_code == email.strip().upper())
    ).first()

    # Password check
    is_valid = False
    if faculty:
        try:
            is_valid = check_password_hash(faculty.password, password)
        except Exception:
            is_valid = (faculty.password == password)

    if not faculty or not is_valid:
        return None, "Invalid email or password."

    # Status check
    if faculty.status != 'Active':
        return None, "Your faculty account is currently inactive. Please contact the administrator."

    # Update last_login timestamp safely
    try:
        faculty.last_login = datetime.utcnow()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[Faculty Service Error] Could not update last_login: {e}")

    return faculty, None


def get_faculty_dashboard_data(faculty_id):
    """
    Retrieves real statistics and assigned subjects for logged-in faculty.
    Returns:
        dict: {
            assigned_subjects_count,
            assigned_divisions_count,
            department,
            status,
            assigned_subjects
        }
    """
    faculty = Faculty.query.get(faculty_id)
    if not faculty:
        return {
            "assigned_subjects_count": 0,
            "assigned_divisions_count": 0,
            "department": "N/A",
            "status": "Inactive",
            "assigned_subjects": []
        }

    # Fetch detailed assigned subjects list filtered strictly by active cycle
    assigned_subjects = get_faculty_assigned_subjects(faculty.id, filter_by_cycle=True)
    assigned_subjects_count = len(assigned_subjects)

    # Count distinct divisions in active cycle
    distinct_divisions = set()
    for a in assigned_subjects:
        if a.get("division"):
            distinct_divisions.add(a["division"].strip().upper())
    assigned_divisions_count = len(distinct_divisions)

    return {
        "assigned_subjects_count": assigned_subjects_count,
        "assigned_divisions_count": assigned_divisions_count,
        "department": faculty.department or "General",
        "status": faculty.status,
        "assigned_subjects": assigned_subjects
    }


def get_faculty_assigned_subjects(faculty_id, filter_by_cycle=True):
    """
    Fetches all subjects assigned to the logged-in faculty with subject details.
    Queries: FacultySubjectAssignment joined with Subject.
    If filter_by_cycle is True (default), filters subjects dynamically according to
    the current Academic Settings semester cycle (Odd: Sem 1, 3, 5 / Even: Sem 2, 4, 6).
    Returns:
        list[dict]: List of assigned subjects with code, name, semester, division, status.
    """
    from sqlalchemy.orm import joinedload
    assignments = (
        FacultySubjectAssignment.query
        .options(joinedload(FacultySubjectAssignment.subject))
        .filter_by(faculty_id=faculty_id, status='Active')
        .all()
    )

    active_sems = None
    if filter_by_cycle:
        from services.academic_service import get_active_semesters
        active_sems = get_active_semesters()

    result = []
    for a in assignments:
        sub = a.subject
        if not sub:
            continue
        if filter_by_cycle and active_sems and (sub.semester not in active_sems):
            continue

        result.append({
            "assignment_id": a.id,
            "subject_id": sub.id,
            "subject_code": sub.subject_code,
            "subject_name": sub.subject_name,
            "course": sub.course,
            "semester": sub.semester,
            "division": a.division or "A",
            "subject_type": sub.subject_type,
            "credits": sub.credits,
            "status": a.status
        })

    # Sort deterministically by semester, subject_code, division
    result.sort(key=lambda item: (item["semester"] or 0, item["subject_code"], item["division"]))
    return result


def change_faculty_password(faculty_id, current_password, new_password, confirm_password):
    """
    Verifies current password and updates with a new Werkzeug hash.
    Sets password_changed = True.
    Returns:
        tuple: (True, "Success message") or (False, "Error message")
    """
    faculty = Faculty.query.get(faculty_id)
    if not faculty:
        return False, "Faculty account not found."

    if not current_password or not new_password or not confirm_password:
        return False, "Please fill in all password fields."

    # Verify current password
    is_valid = False
    try:
        is_valid = check_password_hash(faculty.password, current_password)
    except Exception:
        is_valid = (faculty.password == current_password)

    if not is_valid:
        return False, "Current password is incorrect."

    # Validate new password match
    if new_password != confirm_password:
        return False, "New password and confirmation password do not match."

    # Security check: Minimum length
    if len(new_password) < 6:
        return False, "New password must be at least 6 characters long."

    # Prevent setting the exact same password
    if current_password == new_password:
        return False, "New password cannot be the same as your current password."

    # Hash new password
    hashed_password = generate_password_hash(new_password)

    try:
        faculty.password = hashed_password
        faculty.password_changed = True
        faculty.updated_at = datetime.utcnow()
        db.session.commit()
        return True, "Password changed successfully."
    except Exception as e:
        db.session.rollback()
        return False, f"Database error while changing password: {str(e)}"


def get_faculty_marks_students(faculty_id, subject_id, division, semester=None, academic_year="2026-27"):
    """
    Fetches filtered student list and internal marks status for a logged-in faculty.

    Enforces:
    1. Logged-in faculty assignment check (FacultySubjectAssignment)
    2. Filters by selected subject, division, semester, academic_year
    3. Queries active students in division/semester directly
    4. Retrieves existing InternalMark breakdown for each student

    Returns:
        tuple: (data_dict, None) or (None, error_message)
    """
    if not faculty_id:
        return None, "Unauthorized access."

    # 1. Check faculty subject assignment
    assignment = FacultySubjectAssignment.query.filter_by(
        faculty_id=faculty_id,
        subject_id=subject_id,
        division=division,
        status='Active'
    ).first()

    if not assignment:
        return None, "Access denied: Selected subject and division are not assigned to you."

    subject = db.session.get(Subject, subject_id)
    if not subject:
        return None, "Subject not found."

    sem = semester if semester else subject.semester
    ay = academic_year.strip() if academic_year else "2026-27"

    # 2. Query active students directly for this semester & division
    students = Student.query.filter(
        Student.semester == sem,
        Student.division == division,
        Student.status == 'Active'
    ).order_by(db.cast(Student.roll_number, db.Integer).asc(), Student.full_name.asc()).all()

    student_list = []
    for st in students:
        mark = InternalMark.query.filter_by(
            student_id=st.id,
            subject_id=subject.id,
            semester=sem,
            academic_year=ay
        ).first()

        status = "Not Entered"
        breakdown = None
        if mark:
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
                elif len(non_nulls) > 0:
                    status = "Partially Entered"
                else:
                    status = "Saved" if mark.marks_obtained is not None else "Not Entered"
            else:
                prac_vals = [mark.internal_exam, mark.practical_eval, mark.journal]
                non_nulls = [v for v in prac_vals if v is not None]
                if len(non_nulls) == len(prac_vals):
                    status = "Saved"
                elif len(non_nulls) > 0:
                    status = "Partially Entered"
                else:
                    status = "Saved" if mark.marks_obtained is not None else "Not Entered"

        student_list.append({
            "id": st.id,
            "roll_number": st.roll_number,
            "enrollment_no": st.enrollment_no,
            "full_name": st.full_name,
            "division": st.division,
            "semester": st.semester,
            "marks_status": status,
            "marks_obtained": mark.marks_obtained if mark else None,
            "max_marks": mark.max_marks if mark else (subject.internal_marks or (50 if subject.subject_type == 'Theory' else 25)),
            "breakdown": breakdown
        })

    def _parse_roll_num(val):
        if val is None:
            return 999999
        digits = "".join(c for c in str(val).strip() if c.isdigit())
        return int(digits) if digits else 999999

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
            "components": comp_config["components"] if comp_config else [],
            "maxes": comp_config["maxes"] if comp_config else {},
            "max_sum": comp_config["max_sum"] if comp_config else subject.internal_marks
        },
        "division": division,
        "semester": sem,
        "academic_year": ay,
        "students": student_list
    }, None


def get_faculty_assigned_students(faculty_id, subject_id, division, semester=None, academic_year="2026-27"):
    """
    Fetches filtered student list for a logged-in faculty member.
    Enforces authorization check against FacultySubjectAssignment for faculty_id + subject_id + division.
    Sorts student list numerically ascending by Roll Number (1, 2, 3, 5, 8, 9, 12, 13).
    """
    if not faculty_id:
        return None, "Unauthorized access."

    # 1. Verify Faculty Assignment
    assignment = FacultySubjectAssignment.query.filter_by(
        faculty_id=faculty_id,
        subject_id=subject_id,
        division=division,
        status='Active'
    ).first()

    if not assignment:
        return None, "Access denied: Selected subject and division are not assigned to you."

    subject = db.session.get(Subject, subject_id)
    if not subject:
        return None, "Subject not found."

    sem = semester if (semester and semester > 0) else subject.semester
    ay = academic_year.strip() if academic_year else "2026-27"

    # 2. Query active students directly for this semester & division
    students = Student.query.filter(
        Student.semester == sem,
        Student.division == division,
        Student.status == 'Active'
    ).order_by(db.cast(Student.roll_number, db.Integer).asc(), Student.full_name.asc()).all()

    student_list = []
    for st in students:
        photo_url = None
        if st.profile_photo and st.profile_photo != 'default-avatar.png':
            photo_url = f"/uploads/student/{st.profile_photo}"

        student_list.append({
            "id": st.id,
            "roll_number": st.roll_number,
            "enrollment_no": st.enrollment_no,
            "full_name": st.full_name,
            "email": st.email,
            "mobile": st.mobile or "N/A",
            "dob": str(st.dob) if st.dob else "N/A",
            "course": st.course or "BCA",
            "semester": st.semester,
            "division": st.division,
            "academic_year": st.academic_year or ay,
            "status": st.status,
            "profile_photo": photo_url,
            "address": getattr(st, "address", "N/A"),
            "city": getattr(st, "city", "N/A"),
            "state": getattr(st, "state", "N/A"),
            "pincode": getattr(st, "pincode", "N/A")
        })

    def _parse_roll_num(val):
        if val is None:
            return 999999
        digits = "".join(c for c in str(val).strip() if c.isdigit())
        return int(digits) if digits else 999999

    student_list.sort(key=lambda s: (_parse_roll_num(s.get("roll_number")), s.get("full_name", "").lower()))

    return {
        "subject": {
            "id": subject.id,
            "subject_code": subject.subject_code,
            "subject_name": subject.subject_name,
            "subject_type": subject.subject_type,
            "semester": subject.semester,
            "course": subject.course
        },
        "division": division,
        "semester": sem,
        "academic_year": ay,
        "students": student_list
    }, None


