"""
CampusSync ERP - Student Service
================================
File: services/student_service.py
"""

from werkzeug.security import generate_password_hash
from models import Student
from extensions import db
from utils.helpers import generate_roll_number, generate_enrollment_no
from mail import send_student_welcome_email

def get_all_students_sorted(semester=None):
    """
    Fetches student records sorted numerically by Roll Number.
    Strictly aligns with the current Academic Lifecycle:
    - If no specific semester is provided, returns students belonging to the active cycle semesters
      (Odd cycle: Sem 1, 3, 5; Even cycle: Sem 2, 4, 6).
    - If a specific semester is requested, validates that it belongs to active semesters.
    """
    from services.academic_service import get_academic_settings, get_active_semesters

    academic = get_academic_settings()
    cycle = academic.semester_cycle if academic else 'Odd'
    active_semesters = get_active_semesters(cycle)

    query = Student.query

    # Server-side filtering by semester if valid semester value is provided
    if semester is not None:
        try:
            sem_num = int(semester)
            if sem_num in active_semesters:
                query = query.filter(Student.semester == sem_num)
            else:
                # Requested semester is not part of current active cycle
                query = query.filter(Student.semester == sem_num, Student.semester.in_(active_semesters))
        except (ValueError, TypeError):
            query = query.filter(Student.semester.in_(active_semesters))
    else:
        # Show all students belonging to the current active cycle
        query = query.filter(Student.semester.in_(active_semesters))

    from models import InternalMark

    # Fetch set of student IDs that have saved internal marks
    marks_student_ids = set(
        rec.student_id for rec in InternalMark.query.with_entities(InternalMark.student_id).all()
    )

    students = query.order_by(db.cast(Student.roll_number, db.Integer).asc()).all()
    student_list = []
    for s in students:
        dob_str = str(s.dob) if s.dob else None
        student_list.append({
            "id": s.id,
            "roll_number": s.roll_number,
            "enrollment_no": s.enrollment_no,
            "full_name": s.full_name,
            "email": s.email,
            "mobile": s.mobile,
            "dob": dob_str,
            "course": s.course,
            "semester": s.semester,
            "division": s.division,
            "academic_year": s.academic_year,
            "status": s.status,
            "has_marks": (s.id in marks_student_ids)
        })
    return student_list

def create_student(full_name, email, mobile, parsed_dob, course, parsed_semester, division=None, academic_year=None):
    """
    Creates a new Student record with auto-generated Roll & Enrollment numbers.
    Supports custom academic_year from manual input, falling back to central settings if absent.
    Allows enrollment into any valid semester without changing central cycle settings.
    Returns tuple: (new_student, None) on success, or (None, error_msg) on failure.
    """
    from services.college_service import get_college_settings
    from services.academic_service import get_academic_settings
    from utils.helpers import calculate_division_from_roll

    academic = get_academic_settings()

    if Student.query.filter_by(email=email).first():
        return None, ("A student with this email already exists.", {"email": "Already exists."}, 409)

    if mobile and Student.query.filter_by(mobile=mobile).first():
        return None, ("A student with this mobile number already exists.", {"mobile": "Already exists."}, 409)

    try:
        # Get active settings if course, academic_year or division are missing
        college = get_college_settings()
        course_name = (course or (college.college_type if college and college.college_type else 'BCA')).strip()

        if academic_year and str(academic_year).strip():
            academic_year_val = str(academic_year).strip()
        else:
            academic_year_val = (academic.academic_year if academic and academic.academic_year else '2026-27').strip()

        students_per_div = getattr(academic, 'students_per_division', 70) or 70

        # Generate next roll number for this semester and academic year
        roll_number = generate_roll_number(
            semester=parsed_semester,
            academic_year=academic_year_val,
            course=course_name
        )
        
        # Determine division
        final_division = division.strip() if division and str(division).strip() else calculate_division_from_roll(roll_number, students_per_division=students_per_div)

        enrollment_no = generate_enrollment_no(course_name)
        default_pwd = mobile if mobile else '0000000000'
        hashed_password = generate_password_hash(default_pwd)

        new_student = Student(
            roll_number=roll_number,
            enrollment_no=enrollment_no,
            full_name=full_name,
            email=email,
            mobile=mobile,
            dob=parsed_dob,
            course=course_name,
            semester=parsed_semester,
            division=final_division,
            academic_year=academic_year_val,
            password=hashed_password,
            status='Active',
            password_changed=0
        )
        db.session.add(new_student)
        db.session.commit()

        # Create EmailLog tracking record (status=PENDING) and dispatch background email asynchronously
        try:
            from services.email_service import create_email_log, dispatch_background_emails
            log_entry = create_email_log(new_student.id, new_student.email, email_type='WELCOME')
            db.session.commit()
            dispatch_background_emails([(new_student.id, log_entry.id, default_pwd)])
        except Exception as mail_err:
            print(f"[Student Service] Email tracking dispatch notice: {mail_err}")

        return new_student, None
    except Exception as e:
        db.session.rollback()
        error_msg = str(e)
        if "Duplicate entry" in error_msg:
            if "uq_academic_course_sem_roll" in error_msg or "uq_academic_course_roll" in error_msg:
                friendly_msg = f"A student with this Roll Number already exists in Semester {parsed_semester} for {course_name} ({academic_year_val})."
            elif "email" in error_msg:
                friendly_msg = "A student with this Email address already exists."
            elif "enrollment_no" in error_msg:
                friendly_msg = "A student with this Enrollment Number already exists."
            elif "mobile" in error_msg:
                friendly_msg = "A student with this Mobile Number already exists."
            else:
                friendly_msg = "A student with these details already exists in the system."
            return None, (friendly_msg, None, 409)
        return None, (f"Database error: {error_msg}", None, 500)

def remove_student_by_id(student_id):
    """
    Deletes student record by ID.
    Returns tuple: (success_dict, None) on success or (None, error_tuple) on error.
    """
    try:
        student = Student.query.get(student_id)
        if not student:
            return None, ("Student record not found.", 404)

        student_name = student.full_name
        roll_no = student.roll_number

        db.session.delete(student)
        db.session.commit()
        return {"student_name": student_name, "roll_no": roll_no}, None
    except Exception as e:
        db.session.rollback()
        return None, (f"Database error while deleting student: {str(e)}", 500)


# ==============================================================================
# STUDENT AUTHENTICATION & DASHBOARD SERVICE FUNCTIONS
# ==============================================================================

def authenticate_student(email, password):
    """
    Handles Student Login authentication logic using SQLAlchemy ORM.
    1. Finds student by email address.
    2. Verifies hashed password using check_password_hash().
    3. Checks if student status is Active.
    4. Updates last_login timestamp upon successful login.
    Returns: (student, None) on success, or (None, error_message) on failure.
    """
    from datetime import datetime
    from werkzeug.security import check_password_hash
    from models import ArchivedStudent

    cleaned_email = email.strip().lower()

    # 1. Strictly block graduated / archived students from logging into the active portal
    archived_student = ArchivedStudent.query.filter(
        (ArchivedStudent.email == cleaned_email) | (ArchivedStudent.enrollment_no == email.strip())
    ).first()
    if archived_student:
        return None, "This account belongs to a graduated / archived student. Portal login is disabled. Please view your results via the 'Old Student Result' portal on the home page."

    # 2. Find student by email or enrollment number
    student = Student.query.filter(
        (Student.email == cleaned_email) | (Student.enrollment_no == email.strip())
    ).first()

    # 3. Verify password if student exists
    is_valid_password = False
    if student:
        try:
            is_valid_password = check_password_hash(student.password, password)
        except Exception:
            # Fallback for plain text password comparison if unhashed
            is_valid_password = (student.password == password)

    # 4. Check credentials
    if not student or not is_valid_password:
        return None, "Invalid email or password."

    # 5. Check student account status
    if student.status != 'Active':
        return None, "Your student account is inactive or graduated. Portal login is disabled. Please use the 'Old Student Result' portal on the home page."

    # Update last_login timestamp
    try:
        student.last_login = datetime.utcnow()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"[Student Service Error] Could not update last_login: {e}")

    return student, None


def get_student_by_id(student_id):
    """
    Fetch student details by session student ID.
    """
    if not student_id:
        return None
    return Student.query.get(int(student_id))


def get_student_dict(student_id):
    """
    Returns student details as a dictionary suitable for API responses and pre-filling the Edit modal.
    """
    student = get_student_by_id(student_id)
    if not student:
        return None
    return {
        "id": student.id,
        "roll_number": student.roll_number,
        "enrollment_no": student.enrollment_no,
        "full_name": student.full_name,
        "email": student.email,
        "mobile": student.mobile or '',
        "dob": student.dob.strftime('%Y-%m-%d') if student.dob else '',
        "course": student.course or 'BCA',
        "semester": student.semester,
        "division": student.division or 'A',
        "academic_year": student.academic_year or '2026-27',
        "status": student.status or 'Active',
        "profile_photo": student.profile_photo or 'default-avatar.png'
    }


def update_student(student_id, form_data):
    """
    Updates an existing student record with validation and unique constraint safety.
    Returns tuple: (student, error_message, status_code)
    """
    student = Student.query.get(int(student_id))
    if not student:
        return None, "Student record not found.", 404

    full_name = (form_data.get('full_name') or '').strip()
    email = (form_data.get('email') or '').strip().lower()
    mobile = (form_data.get('mobile') or '').strip()
    dob_raw = form_data.get('dob')
    course = (form_data.get('course') or '').strip()
    semester = form_data.get('semester')
    division = (form_data.get('division') or '').strip()
    academic_year = (form_data.get('academic_year') or '').strip()
    status = (form_data.get('status') or 'Active').strip()

    if not full_name or len(full_name) < 3:
        return None, "Full name must be at least 3 characters.", 400
    if not all(c.isalpha() or c.isspace() for c in full_name):
        return None, "Full name may only contain alphabets and spaces.", 400

    if not email or '@' not in email or '.' not in email:
        return None, "Please provide a valid email address.", 400

    # Email uniqueness excluding current student
    existing_email = Student.query.filter(Student.email == email, Student.id != student.id).first()
    if existing_email:
        return None, "Another student is already registered with this email address.", 409

    # Mobile number uniqueness excluding current student
    if mobile:
        if not mobile.isdigit() or len(mobile) != 10:
            return None, "Mobile number must be exactly 10 digits.", 400
        existing_mobile = Student.query.filter(Student.mobile == mobile, Student.id != student.id).first()
        if existing_mobile:
            return None, "Another student is already registered with this mobile number.", 409

    if not course:
        return None, "Please select a course.", 400

    try:
        parsed_semester = int(semester)
        if not (1 <= parsed_semester <= 8):
            return None, "Semester must be between 1 and 8.", 400
    except (ValueError, TypeError):
        return None, "Invalid semester selected.", 400

    if not division:
        return None, "Please select a division.", 400

    if not academic_year:
        from services.academic_service import get_academic_settings
        curr_acad = get_academic_settings()
        academic_year = curr_acad.academic_year if curr_acad else '2026-27'

    parsed_dob = None
    if dob_raw:
        try:
            from datetime import datetime, date
            parsed_dob = datetime.strptime(str(dob_raw).strip(), '%Y-%m-%d').date()
            if parsed_dob >= date.today():
                return None, "Date of birth must be in the past.", 400
        except ValueError:
            return None, "Invalid date of birth format.", 400

    if status not in ['Active', 'Inactive']:
        status = 'Active'

    try:
        from utils.helpers import generate_roll_number

        # If semester, academic_year, or course changed, check if roll_number collides in destination batch
        if (parsed_semester != student.semester or 
            academic_year != student.academic_year or 
            course != student.course):

            conflict = Student.query.filter(
                Student.id != student.id,
                Student.academic_year == academic_year,
                Student.course == course,
                Student.semester == parsed_semester,
                Student.roll_number == student.roll_number
            ).first()

            if conflict:
                student.roll_number = generate_roll_number(
                    semester=parsed_semester,
                    academic_year=academic_year,
                    course=course
                )

        student.full_name = full_name
        student.email = email
        student.mobile = mobile or None
        student.dob = parsed_dob
        student.course = course
        student.semester = parsed_semester
        student.division = division
        student.academic_year = academic_year
        student.status = status

        db.session.commit()
        return student, None, 200
    except Exception as e:
        db.session.rollback()
        return None, f"Database error updating student: {str(e)}", 500


def update_student_last_login(student_id):
    """
    Update student last_login timestamp in database.
    """
    try:
        from datetime import datetime
        student = Student.query.get(int(student_id))
        if student:
            student.last_login = datetime.utcnow()
            db.session.commit()
            return True
        return False
    except Exception as e:
        db.session.rollback()
        print(f"[Student Service Error] Failed to update last_login: {e}")
        return False


# ==============================================================================
# BULK STUDENT OPERATIONS
# ==============================================================================

def bulk_change_semester(student_ids, target_semester):
    """
    Bulk updates the semester of selected students and regenerates their roll numbers
    according to the semester-wise roll number generation logic.
    Returns (result_dict, None) on success or (None, error_tuple) on failure.
    """
    # Check if student IDs list is provided
    if not student_ids or not isinstance(student_ids, list):
        return None, ("No students selected.", 400)

    # Validate target semester (must be between 1 and 6)
    try:
        sem_num = int(target_semester)
        if not (1 <= sem_num <= 6):
            return None, ("Invalid semester selected. Must be between 1 and 6.", 400)
    except (ValueError, TypeError):
        return None, ("Invalid target semester.", 400)

    from services.academic_service import get_academic_settings, get_active_semesters
    academic = get_academic_settings()
    active_semesters = get_active_semesters(academic.semester_cycle if academic else 'Odd')

    if sem_num not in active_semesters:
        cycle_name = academic.semester_cycle if academic else 'Odd'
        allowed_str = ', '.join([f"Semester {s}" for s in active_semesters])
        return None, (
            f"Cannot move students to Semester {sem_num}. Current academic cycle is '{cycle_name}', which only permits {allowed_str}.",
            400
        )

    from utils.helpers import generate_roll_number

    try:
        # Fetch matching student records from database
        students = Student.query.filter(Student.id.in_(student_ids)).all()
        if not students:
            return None, ("No valid students found for selected IDs.", 404)

        updated_count = 0
        # Find next available roll number for target semester
        current_max_roll = int(generate_roll_number(semester=sem_num))

        for student in students:
            # Move student to target semester and assign new semester-wise roll number
            student.semester = sem_num
            student.roll_number = str(current_max_roll)
            current_max_roll += 1
            updated_count += 1

        db.session.commit()
        return {"updated_count": updated_count, "target_semester": sem_num}, None
    except Exception as e:
        db.session.rollback()
        return None, (f"Database error during bulk semester update: {str(e)}", 500)


def bulk_delete_students(student_ids):
    """
    Bulk deletes selected student records by ID.
    Returns (result_dict, None) on success or (None, error_tuple) on failure.
    """
    # Check if student IDs list is provided
    if not student_ids or not isinstance(student_ids, list):
        return None, ("No students selected for deletion.", 400)

    try:
        # Fetch matching student records from database
        students = Student.query.filter(Student.id.in_(student_ids)).all()
        if not students:
            return None, ("No valid student records found to delete.", 404)

        deleted_count = len(students)
        # Delete each selected student
        for student in students:
            db.session.delete(student)

        db.session.commit()
        return {"deleted_count": deleted_count}, None
    except Exception as e:
        db.session.rollback()
        return None, (f"Database error during bulk deletion: {str(e)}", 500)
