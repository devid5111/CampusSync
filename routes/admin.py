"""
CampusSync ERP - Admin Routes
=============================
File: routes/admin.py
"""

import os
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, current_app, send_from_directory, flash, send_file
from utils.decorators import admin_required
from extensions import db
from utils.validators import validate_student_input
from services.student_service import get_all_students_sorted, create_student, remove_student_by_id, bulk_change_semester, bulk_delete_students, get_student_dict, update_student
from services.excel_service import process_excel_student_import
from services.auth_service import get_admin_by_id, update_admin_profile, change_admin_password
from services.college_service import get_college_settings, update_college_settings
from services.academic_service import (
    get_academic_settings, update_academic_settings, get_active_semesters,
    get_graduating_students_count, get_odd_semester_students_count, get_even_semester_students_count,
    promote_odd_to_even_students, move_even_to_odd_students
)
from services.subject_service import get_all_subjects, get_subject_by_id, add_subject, update_subject, delete_subject, toggle_subject_status
from services.faculty_service import (
    get_faculty_statistics,
    get_all_faculty,
    get_available_subjects,
    get_faculty_by_id,
    create_faculty,
    update_faculty,
    delete_faculty
)

admin_bp = Blueprint('admin', __name__)

@admin_bp.context_processor
def inject_current_admin():
    """Injects current logged-in Admin object and College Settings into all admin templates."""
    context = dict(current_admin=None, college=get_college_settings())
    if "admin_id" in session:
        from flask import g
        if not hasattr(g, 'current_admin'):
            g.current_admin = get_admin_by_id(session["admin_id"])
        context["current_admin"] = g.current_admin
    return context

# --- Admin Dashboard Route ---
@admin_bp.route('/admin/dashboard', endpoint='admin_dashboard')
def admin_dashboard():
    """Protected Admin Dashboard page with dynamic database metrics."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    from services.admin_dashboard_service import get_admin_dashboard_data
    dash_data = get_admin_dashboard_data()

    return render_template(
        'admin/dashboard.html',
        active_page='dashboard',
        dash_data=dash_data
    )

# --- Student Excel Import Route ---
@admin_bp.route('/admin/import-students', methods=['POST'], endpoint='import_students')
def import_students():
    """Student Excel Import Endpoint."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized. Please login again."}), 401

    if 'excel_file' not in request.files:
        return jsonify({"success": False, "message": "No file received."}), 400

    file = request.files['excel_file']

    if file.filename == '':
        return jsonify({"success": False, "message": "No file selected."}), 400

    if not file.filename.endswith('.xlsx'):
        return jsonify({"success": False, "message": "Only .xlsx files are allowed."}), 400

    try:
        selected_semester = request.form.get('semester', 1, type=int)
        response_data, status_code = process_excel_student_import(file, selected_semester=selected_semester)
        if isinstance(response_data, dict) and "success" not in response_data:
            response_data["success"] = (status_code >= 200 and status_code < 300)
        return jsonify(response_data), status_code
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "added": 0,
            "skipped": 0,
            "errors": [str(e)],
            "message": f"Server processing error: {str(e)}"
        }), 500

# --- Get All Students API Route ---
@admin_bp.route('/admin/api/students', methods=['GET'], endpoint='api_get_students')
def api_get_students():
    """Returns students sorted numerically by Roll Number, with optional semester or query search filter."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized. Please login again."}), 401

    try:
        semester = request.args.get('semester', None)
        query_param = request.args.get('query', '').strip()

        if query_param:
            from models import Student, InternalMark
            students = Student.query.filter(
                (Student.enrollment_no.ilike(f"%{query_param}%")) |
                (Student.roll_number.ilike(f"%{query_param}%")) |
                (Student.full_name.ilike(f"%{query_param}%"))
            ).order_by(Student.semester.asc(), db.cast(Student.roll_number, db.Integer).asc()).all()

            # Medal badge only shows if faculty has saved marks for student's current semester
            current_sem_marks_pairs = set(
                (rec.student_id, rec.semester)
                for rec in InternalMark.query.with_entities(InternalMark.student_id, InternalMark.semester).all()
            )

            student_list = []
            for s in students:
                student_list.append({
                    "id": s.id,
                    "roll_number": s.roll_number,
                    "enrollment_no": s.enrollment_no,
                    "full_name": s.full_name,
                    "email": s.email,
                    "mobile": s.mobile,
                    "dob": str(s.dob) if s.dob else None,
                    "course": s.course,
                    "semester": s.semester,
                    "division": s.division,
                    "academic_year": s.academic_year,
                    "status": s.status,
                    "has_marks": ((s.id, s.semester) in current_sem_marks_pairs)
                })
        else:
            student_list = get_all_students_sorted(semester=semester)

        return jsonify({"success": True, "students": student_list}), 200
    except Exception as e:
        print(f"Error in api_get_students: {e}")
        return jsonify({"success": False, "message": f"Database error: {str(e)}"}), 500

# --- Add Student API Route ---
@admin_bp.route('/admin/api/students/add', methods=['POST'], endpoint='api_add_student')
def api_add_student():
    """Add new student endpoint with server-side validation."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized. Please login again."}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"success": False, "message": "Invalid request data."}), 400

    parsed_data, errors = validate_student_input(data)

    if errors:
        return jsonify({
            "success": False,
            "message": "Please fix the highlighted errors.",
            "errors": errors
        }), 400

    new_student, error_info = create_student(
        full_name=parsed_data['full_name'],
        email=parsed_data['email'],
        mobile=parsed_data['mobile'],
        parsed_dob=parsed_data['dob'],
        course=parsed_data['course'],
        parsed_semester=parsed_data['semester'],
        division=parsed_data['division'],
        academic_year=parsed_data.get('academic_year')
    )

    if error_info:
        message, extra_errors, status_code = error_info
        resp = {"success": False, "message": message}
        if extra_errors:
            resp["errors"] = extra_errors
        return jsonify(resp), status_code

    return jsonify({
        "success": True,
        "message": f"Student '{new_student.full_name}' added! Roll No: {new_student.roll_number}, Enrollment No: {new_student.enrollment_no}. Default password is their mobile number."
    }), 201

# --- Get Single Student API Route ---
@admin_bp.route('/admin/api/students/<int:student_id>', methods=['GET'], endpoint='api_get_single_student')
def api_get_single_student(student_id):
    """Fetches details of a single student for the Edit modal."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized. Please login again."}), 401

    data = get_student_dict(student_id)
    if not data:
        return jsonify({"success": False, "message": "Student not found."}), 404

    return jsonify({"success": True, "student": data}), 200


# --- Update Student API Route ---
@admin_bp.route('/admin/api/students/<int:student_id>/update', methods=['POST'], endpoint='api_update_student')
def api_update_student(student_id):
    """Updates an existing student's record with validation and unique constraint safety."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized. Please login again."}), 401

    payload = request.get_json(silent=True) or request.form
    if not payload:
        return jsonify({"success": False, "message": "No data provided."}), 400

    student, error_msg, status_code = update_student(student_id, payload)
    if error_msg:
        return jsonify({"success": False, "message": error_msg}), status_code

    return jsonify({
        "success": True,
        "message": f"Student '{student.full_name}' updated successfully! Roll No: {student.roll_number}, Semester: {student.semester}, Division: {student.division}.",
        "student": {
            "id": student.id,
            "full_name": student.full_name,
            "roll_number": student.roll_number,
            "semester": student.semester,
            "division": student.division,
            "academic_year": student.academic_year,
            "status": student.status
        }
    }), 200


# --- Delete Student API Route ---
@admin_bp.route('/admin/api/students/delete/<int:student_id>', methods=['POST', 'DELETE'], endpoint='api_delete_student')
def api_delete_student(student_id):
    """Deletes student record by ID."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized. Please login again."}), 401

    result, error_info = remove_student_by_id(student_id)
    if error_info:
        message, status_code = error_info
        return jsonify({"success": False, "message": message}), status_code

    return jsonify({
        "success": True,
        "message": f"Student '{result['student_name']}' (Roll No: {result['roll_no']}) deleted successfully."
    }), 200

# --- Bulk Change Semester API Route ---
@admin_bp.route('/admin/students/bulk-semester', methods=['POST'], endpoint='api_bulk_change_semester')
def api_bulk_change_semester():
    """Bulk updates the semester of selected students and regenerates their roll numbers."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized. Please login again."}), 401

    data = request.get_json(silent=True) or {}
    student_ids = data.get('student_ids', [])
    target_semester = data.get('target_semester', None)

    result, error_info = bulk_change_semester(student_ids, target_semester)
    if error_info:
        message, status_code = error_info
        return jsonify({"success": False, "message": message}), status_code

    return jsonify({
        "success": True,
        "message": f"Moved {result['updated_count']} student(s) to Semester {result['target_semester']} with new Roll Numbers."
    }), 200

# --- Bulk Delete Students API Route ---
@admin_bp.route('/admin/students/bulk-delete', methods=['POST'], endpoint='api_bulk_delete_students')
def api_bulk_delete_students():
    """Bulk deletes selected student records by database ID."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized. Please login again."}), 401

    data = request.get_json(silent=True) or {}
    student_ids = data.get('student_ids', [])

    result, error_info = bulk_delete_students(student_ids)
    if error_info:
        message, status_code = error_info
        return jsonify({"success": False, "message": message}), status_code

    return jsonify({
        "success": True,
        "message": f"Deleted {result['deleted_count']} selected student record(s) successfully."
    }), 200

# --- Admin Sub-page Routes ---
@admin_bp.route('/admin/students', endpoint='admin_students')
def admin_students():
    """Renders Admin Students Management page with optional semester filter."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    academic = get_academic_settings()
    active_semesters = get_active_semesters(academic.semester_cycle if academic else 'Odd')

    # Safely get semester query parameter (e.g. /admin/students?semester=1)
    semester_raw = request.args.get('semester', '').strip()
    selected_semester = None

    # Validate if semester is an integer belonging to the current active cycle
    if semester_raw.isdigit():
        sem_val = int(semester_raw)
        if sem_val in active_semesters:
            selected_semester = sem_val

    return render_template('admin/students.html', selected_semester=selected_semester, academic=academic, active_semesters=active_semesters)

# ==============================================================================
# FACULTY MANAGEMENT ROUTES & API
# ==============================================================================

@admin_bp.route('/admin/faculty', endpoint='admin_faculty')
def admin_faculty():
    """Protected Faculty Management UI Page."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))
    return render_template('admin/faculty.html')


@admin_bp.route('/admin/faculty/data', endpoint='get_faculty_data')
def get_faculty_data():
    """
    Returns live faculty records, statistics, and available subjects.
    Supports server-side search and filtering by status and department.
    """
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized. Please login again."}), 401

    search = request.args.get('search', '').strip()
    status = request.args.get('status', 'all').strip()
    dept = request.args.get('dept', 'all').strip()

    faculty_list = get_all_faculty(search=search, status=status, department=dept)
    stats = get_faculty_statistics()
    subjects = get_available_subjects()

    return jsonify({
        "success": True,
        "faculty": faculty_list,
        "stats": stats,
        "subjects": subjects
    })


@admin_bp.route('/admin/faculty/create', methods=['POST'], endpoint='create_faculty')
def create_faculty_route():
    """Creates a new faculty member with hashed password and subject assignments."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized. Please login again."}), 401

    photo = request.files.get('photo')
    new_faculty, error = create_faculty(request.form, photo_file=photo)

    if error:
        return jsonify({"success": False, "message": error}), 400

    return jsonify({
        "success": True,
        "message": f"Faculty member '{new_faculty.full_name}' was added successfully.",
        "faculty_id": new_faculty.id
    })


@admin_bp.route('/admin/faculty/<int:faculty_id>', endpoint='get_single_faculty')
def get_single_faculty(faculty_id):
    """Fetches details for a single faculty member."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized. Please login again."}), 401

    data = get_faculty_by_id(faculty_id)
    if not data:
        return jsonify({"success": False, "message": "Faculty member not found."}), 404

    return jsonify({"success": True, "faculty": data})


@admin_bp.route('/admin/faculty/<int:faculty_id>/update', methods=['POST'], endpoint='update_faculty')
def update_faculty_route(faculty_id):
    """Updates an existing faculty member's profile and subject assignments."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized. Please login again."}), 401

    photo = request.files.get('photo')
    updated_faculty, error = update_faculty(faculty_id, request.form, photo_file=photo)

    if error:
        return jsonify({"success": False, "message": error}), 400

    return jsonify({
        "success": True,
        "message": f"Faculty profile for '{updated_faculty.full_name}' has been updated successfully.",
        "faculty_id": updated_faculty.id
    })


@admin_bp.route('/admin/faculty/<int:faculty_id>/delete', methods=['POST'], endpoint='delete_faculty')
def delete_faculty_route(faculty_id):
    """Deletes a faculty member and cascades assignments, leaving master subjects untouched."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized. Please login again."}), 401

    success, error = delete_faculty(faculty_id)
    if not success:
        return jsonify({"success": False, "message": error}), 400

    return jsonify({
        "success": True,
        "message": "Faculty member record was deleted successfully."
    })


@admin_bp.route('/admin/faculty/subjects', endpoint='get_faculty_subjects')
def get_faculty_subjects_route():
    """Fetches active master subjects from the database for assignment UI."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized. Please login again."}), 401

    subjects = get_available_subjects()
    return jsonify({"success": True, "subjects": subjects})


@admin_bp.route('/admin/website', endpoint='admin_website')
def admin_website():
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))
    return render_template('admin/website.html')

@admin_bp.route('/admin/reports', endpoint='admin_reports')
def admin_reports():
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))
    
    from models import Student, AcademicSetting
    from services.college_service import get_college_settings
    
    # Fetch distinct academic years from students and academic settings
    from services.academic_service import get_academic_settings, get_active_semesters
    ac_set = get_academic_settings()
    curr_ay = ac_set.academic_year if ac_set and ac_set.academic_year else '2026-27'
    cycle = ac_set.semester_cycle if ac_set else 'Odd'
    active_semesters = get_active_semesters(cycle)

    db_years = [
        row[0] for row in db.session.query(Student.academic_year).distinct().all() if row[0]
    ]
    if curr_ay not in db_years:
        db_years.insert(0, curr_ay)
    
    # Sort descending
    academic_years = sorted(list(set(db_years)), reverse=True)
    college = get_college_settings()

    return render_template(
        'admin/reports.html',
        academic_years=academic_years,
        current_ay=curr_ay,
        college=college,
        active_semesters=active_semesters,
        semester_cycle=cycle,
        active_page='reports'
    )


@admin_bp.route('/admin/api/reports/analytics', methods=['GET'], endpoint='admin_reports_analytics_api')
def admin_reports_analytics_api():
    """
    JSON REST API endpoint serving live aggregated analytics across all 5 report categories.
    Supports dynamic filtering by semester, academic year, and top performers count.
    """
    if "admin_id" not in session:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    from services.report_service import get_reports_analytics_data

    sem_raw = request.args.get('semester', '').strip()
    semester = None
    if sem_raw.isdigit() and 1 <= int(sem_raw) <= 6:
        semester = int(sem_raw)

    academic_year = request.args.get('academic_year', '').strip() or None
    top_limit = request.args.get('top_limit', '10').strip()

    try:
        data = get_reports_analytics_data(
            semester=semester,
            academic_year=academic_year,
            top_limit=top_limit
        )
        return jsonify(data)
    except Exception as e:
        current_app.logger.error(f"[Reports Analytics API Error] {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/admin/reports/export/excel', methods=['GET'], endpoint='admin_reports_export_excel')
def admin_reports_export_excel():
    """Generates and downloads a comprehensive 4-sheet Excel report workbook."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    from services.report_service import generate_reports_excel

    sem_raw = request.args.get('semester', '').strip()
    semester = int(sem_raw) if sem_raw.isdigit() and 1 <= int(sem_raw) <= 6 else None
    academic_year = request.args.get('academic_year', '').strip() or None
    top_limit = request.args.get('top_limit', '10').strip()

    try:
        excel_stream = generate_reports_excel(
            semester=semester,
            academic_year=academic_year,
            top_limit=top_limit
        )
        filename = f"CampusSync_Institutional_Analytics_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return send_file(
            excel_stream,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        current_app.logger.error(f"[Reports Excel Export Error] {e}", exc_info=True)
        flash(f"Failed to export Excel report: {str(e)}", "danger")
        return redirect(url_for('admin_reports'))


@admin_bp.route('/admin/reports/export/defaulters-pdf', methods=['GET'], endpoint='admin_reports_export_defaulters_pdf')
def admin_reports_export_defaulters_pdf():
    """Generates and downloads an official A4 Attendance Defaulter Notice PDF for the college notice board."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    from services.report_service import generate_defaulters_notice_pdf

    sem_raw = request.args.get('semester', '').strip()
    semester = int(sem_raw) if sem_raw.isdigit() and 1 <= int(sem_raw) <= 6 else None
    academic_year = request.args.get('academic_year', '').strip() or None

    try:
        pdf_stream = generate_defaulters_notice_pdf(
            semester=semester,
            academic_year=academic_year
        )
        filename = f"Official_Attendance_Defaulter_Notice_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return send_file(
            pdf_stream,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        current_app.logger.error(f"[Defaulters PDF Export Error] {e}", exc_info=True)
        flash(f"Failed to export Defaulters Notice PDF: {str(e)}", "danger")
        return redirect(url_for('admin_reports'))


@admin_bp.route('/admin/reports/export/toppers-pdf', methods=['GET'], endpoint='admin_reports_export_toppers_pdf')
def admin_reports_export_toppers_pdf():
    """Generates and downloads an official Executive College Toppers Merit List PDF with signatures."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    from services.report_service import generate_toppers_list_pdf

    sem_raw = request.args.get('semester', '').strip()
    semester = int(sem_raw) if sem_raw.isdigit() and 1 <= int(sem_raw) <= 6 else None
    academic_year = request.args.get('academic_year', '').strip() or None
    top_limit_raw = request.args.get('top_limit', '10').strip()
    top_limit = int(top_limit_raw) if top_limit_raw.isdigit() and int(top_limit_raw) in [3, 5, 10, 20] else 10

    try:
        pdf_stream = generate_toppers_list_pdf(
            semester=semester,
            academic_year=academic_year,
            top_limit=top_limit
        )
        filename = f"College_Toppers_Merit_List_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return send_file(
            pdf_stream,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        current_app.logger.error(f"[Toppers PDF Export Error] {e}", exc_info=True)
        flash(f"Failed to export Toppers Merit List PDF: {str(e)}", "danger")
        return redirect(url_for('admin_reports'))


@admin_bp.route('/admin/reports/export/master-pdf', methods=['GET'], endpoint='admin_reports_export_master_pdf')
def admin_reports_export_master_pdf():
    """Generates and downloads the comprehensive Master Institutional Performance & Audit Report PDF."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    from services.report_service import generate_master_institutional_report_pdf

    sem_raw = request.args.get('semester', '').strip()
    semester = int(sem_raw) if sem_raw.isdigit() and 1 <= int(sem_raw) <= 6 else None
    academic_year = request.args.get('academic_year', '').strip() or None

    try:
        pdf_stream = generate_master_institutional_report_pdf(
            semester=semester,
            academic_year=academic_year
        )
        filename = f"Master_Institutional_Performance_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        return send_file(
            pdf_stream,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        current_app.logger.error(f"[Master PDF Export Error] {e}", exc_info=True)
        flash(f"Failed to export Master Institutional Report PDF: {str(e)}", "danger")
        return redirect(url_for('admin_reports'))

# ==============================================================================
# SUBJECT MANAGEMENT ROUTES
# ==============================================================================

@admin_bp.route('/admin/subjects', endpoint='admin_subjects')
def admin_subjects():
    """Renders Subject Management page with optional semester filter."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    semester_raw = request.args.get('semester', '').strip()
    selected_semester = None
    if semester_raw.isdigit():
        sem_val = int(semester_raw)
        if 1 <= sem_val <= 6:
            selected_semester = sem_val

    college = get_college_settings()
    college_type = college.college_type if college and college.college_type else 'BCA'

    subjects = get_all_subjects(semester=selected_semester, course=college_type)

    return render_template(
        'admin/subjects.html',
        subjects=subjects,
        selected_semester=selected_semester,
        college_type=college_type,
        active_page='subjects'
    )

@admin_bp.route('/admin/subjects/add', methods=['POST'], endpoint='add_subject')
def add_subject_route():
    """Handles adding a new subject."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    college = get_college_settings()
    college_type = college.college_type if college and college.college_type else 'BCA'

    new_subject, error = add_subject(request.form, default_course=college_type)

    if error:
        flash(error, 'danger')
    else:
        flash(f"Subject '{new_subject.subject_name}' ({new_subject.subject_code}) added successfully!", 'success')

    return redirect(url_for('admin_subjects'))

@admin_bp.route('/admin/subjects/edit/<int:subject_id>', methods=['POST'], endpoint='edit_subject')
def edit_subject_route(subject_id):
    """Handles updating existing subject details."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    subject, error = update_subject(subject_id, request.form)

    if error:
        flash(error, 'danger')
    else:
        flash(f"Subject '{subject.subject_name}' ({subject.subject_code}) updated successfully!", 'success')

    return redirect(url_for('admin_subjects'))

@admin_bp.route('/admin/subjects/delete/<int:subject_id>', methods=['POST'], endpoint='delete_subject')
def delete_subject_route(subject_id):
    """Handles deleting a subject by ID."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    success, error = delete_subject(subject_id)

    if error:
        flash(error, 'danger')
    else:
        flash('Subject deleted successfully!', 'success')

    return redirect(url_for('admin_subjects'))

@admin_bp.route('/admin/subjects/toggle-status/<int:subject_id>', methods=['POST'], endpoint='toggle_subject_status')
def toggle_subject_status_route(subject_id):
    """Handles toggling subject active/inactive status."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    subject, error = toggle_subject_status(subject_id)

    if error:
        flash(error, 'danger')
    else:
        flash(f"Subject '{subject.subject_code}' status changed to {subject.status}.", 'success')

    return redirect(url_for('admin_subjects'))

# ==============================================================================
# ADMIN SETTINGS ROUTES
# ==============================================================================

@admin_bp.route('/admin/settings', endpoint='admin_settings')
@admin_bp.route('/admin/academic-settings', endpoint='admin_academic_settings')
def admin_settings():
    """Renders the Admin Settings page with current logged-in admin data and academic settings."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    admin = get_admin_by_id(session["admin_id"])
    academic = get_academic_settings()
    active_semesters = get_active_semesters(academic.semester_cycle if academic else 'Odd')
    sem6_count = get_graduating_students_count(academic.academic_year if academic else None, semester=6)
    odd_students_summary = get_odd_semester_students_count(academic.academic_year if academic else None)
    even_students_summary = get_even_semester_students_count(academic.academic_year if academic else None)
    return render_template(
        'admin/settings.html',
        admin=admin,
        academic=academic,
        active_semesters=active_semesters,
        sem6_count=sem6_count,
        odd_students_summary=odd_students_summary,
        even_students_summary=even_students_summary
    )

@admin_bp.route('/admin/academic-settings/update', methods=['POST'], endpoint='update_academic_settings')
def update_academic_settings_route():
    """Handles updating Academic Year & Semester Cycle Settings."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    settings, error, archived_count, promoted_count = update_academic_settings(request.form)

    if error:
        flash(error, 'danger')
    else:
        success_msgs = [f'Academic Settings updated successfully! Academic Year is now {settings.academic_year} ({settings.semester_cycle} Cycle).']
        if promoted_count > 0:
            if settings.semester_cycle == 'Even':
                success_msgs.append(f'{promoted_count} active student(s) successfully advanced to Even semester (Sem 2, 4, 6).')
            else:
                success_msgs.append(f'{promoted_count} active student(s) successfully transitioned to Odd semester (Sem 1, 3, 5).')
        if archived_count > 0:
            success_msgs.append(f'{archived_count} graduating Semester 6 student(s) archived & their status set to Inactive (login access disabled).')
        flash(' '.join(success_msgs), 'success')

    return redirect(url_for('admin_settings'))


@admin_bp.route('/admin/academic-settings/sync-students', methods=['POST'], endpoint='sync_cycle_students')
def sync_cycle_students_route():
    """Direct one-click synchronization to align active student semesters with the current academic cycle."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    academic = get_academic_settings()
    cycle = academic.semester_cycle if academic else 'Odd'
    target_cycle = request.form.get('target_cycle', cycle).strip()

    if target_cycle == 'Even':
        moved, err = promote_odd_to_even_students(academic_year=academic.academic_year)
        if err:
            flash(f"Error syncing students to Even cycle: {err}", "danger")
        else:
            flash(f"Successfully aligned {moved} active student(s) to Even Semester (Sem 2, 4, 6).", "success")
    else:
        moved, err = move_even_to_odd_students(academic_year=academic.academic_year)
        if err:
            flash(f"Error syncing students to Odd cycle: {err}", "danger")
        else:
            flash(f"Successfully aligned {moved} active student(s) to Odd Semester (Sem 1, 3, 5).", "success")

    return redirect(url_for('admin_settings'))


@admin_bp.route('/admin/settings/update-profile', methods=['POST'], endpoint='update_admin_profile')
def update_admin_profile_route():
    """Handles updating Admin profile details and photo upload."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    full_name = request.form.get('full_name', '').strip()
    email = request.form.get('email', '').strip().lower()
    mobile = request.form.get('mobile', '').strip()

    # Handle profile photo upload
    photo_filename = None
    if 'profile_photo' in request.files:
        file = request.files['profile_photo']
        if file and file.filename != '':
            ext = os.path.splitext(file.filename)[1].lower()
            if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                photo_filename = f"admin_{session['admin_id']}_{int(datetime.utcnow().timestamp())}{ext}"
                upload_folder = os.path.join(current_app.root_path, 'uploads', 'admin')
                os.makedirs(upload_folder, exist_ok=True)
                file.save(os.path.join(upload_folder, photo_filename))

    admin, error = update_admin_profile(
        admin_id=session["admin_id"],
        full_name=full_name,
        email=email,
        mobile=mobile,
        profile_photo_filename=photo_filename
    )

    if error:
        flash(error, 'danger')
    else:
        flash('Profile details updated successfully!', 'success')

    return redirect(url_for('admin_settings'))

@admin_bp.route('/admin/settings/change-password', methods=['POST'], endpoint='change_admin_password')
def change_admin_password_route():
    """Handles changing Admin password."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')

    if not current_password or not new_password or not confirm_password:
        flash('Please fill in all password fields.', 'danger')
        return redirect(url_for('admin_settings'))

    if new_password != confirm_password:
        flash('New password and confirm password do not match.', 'danger')
        return redirect(url_for('admin_settings'))

    if len(new_password) < 6:
        flash('New password must be at least 6 characters long.', 'danger')
        return redirect(url_for('admin_settings'))

    success, message = change_admin_password(
        admin_id=session["admin_id"],
        current_password=current_password,
        new_password=new_password
    )

    if success:
        flash(message, 'success')
    else:
        flash(message, 'danger')

    return redirect(url_for('admin_settings'))


# ==============================================================================
# COLLEGE SETTINGS ROUTES
# ==============================================================================

@admin_bp.route('/admin/college-settings', endpoint='admin_college_settings')
def admin_college_settings():
    """Renders the College Information Settings page."""
    # Check admin session
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    # Fetch college settings from service
    college = get_college_settings()
    return render_template('admin/college-settings.html', college=college, active_page='college_settings')

@admin_bp.route('/admin/college-settings/update', methods=['POST'], endpoint='update_college_settings')
def update_college_settings_route():
    """Handles updating College Information and Logo Upload."""
    # Check admin session
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    logo_filename = None
    stamp_filename = None
    signature_filename = None
    upload_folder = os.path.join(current_app.root_path, 'uploads', 'college')
    os.makedirs(upload_folder, exist_ok=True)
    allowed_exts = ['.jpg', '.jpeg', '.png', '.gif', '.webp']

    # 1. Upload college logo if provided
    if 'logo' in request.files:
        file = request.files['logo']
        if file and file.filename != '':
            ext = os.path.splitext(file.filename)[1].lower()
            if ext in allowed_exts:
                logo_filename = f"college_logo_{int(datetime.utcnow().timestamp())}{ext}"
                file.save(os.path.join(upload_folder, logo_filename))
            else:
                flash('Invalid logo image format. Allowed: JPG, PNG, GIF, WEBP.', 'danger')
                return redirect(url_for('admin_college_settings'))

    # 2. Upload college official stamp / seal if provided
    if 'college_stamp' in request.files:
        file = request.files['college_stamp']
        if file and file.filename != '':
            ext = os.path.splitext(file.filename)[1].lower()
            if ext in allowed_exts:
                stamp_filename = f"college_stamp_{int(datetime.utcnow().timestamp())}{ext}"
                file.save(os.path.join(upload_folder, stamp_filename))
            else:
                flash('Invalid stamp image format. Allowed: JPG, PNG, GIF, WEBP.', 'danger')
                return redirect(url_for('admin_college_settings'))

    # 3. Upload principal signature if provided
    if 'principal_signature' in request.files:
        file = request.files['principal_signature']
        if file and file.filename != '':
            ext = os.path.splitext(file.filename)[1].lower()
            if ext in allowed_exts:
                signature_filename = f"principal_sig_{int(datetime.utcnow().timestamp())}{ext}"
                file.save(os.path.join(upload_folder, signature_filename))
            else:
                flash('Invalid signature image format. Allowed: JPG, PNG, GIF, WEBP.', 'danger')
                return redirect(url_for('admin_college_settings'))

    # Update database
    college, error = update_college_settings(
        request.form,
        logo_filename=logo_filename,
        stamp_filename=stamp_filename,
        signature_filename=signature_filename
    )

    if error:
        flash(error, 'danger')
    else:
        flash('College settings, official stamp, and signature updated successfully!', 'success')

    return redirect(url_for('admin_college_settings'))


# --- Uploads File Serving Route ---
@admin_bp.route('/uploads/<path:filename>', endpoint='serve_uploads')
def serve_uploads(filename):
    """Serves uploaded files from the uploads directory with caching enabled for high performance."""
    uploads_dir = os.path.join(current_app.root_path, 'uploads')
    return send_from_directory(uploads_dir, filename, max_age=86400)

# --- Live Browser Email Preview Route ---
@admin_bp.route('/email-preview', endpoint='email_preview')
def email_preview():
    """Live browser preview route for Student Welcome Email template"""
    logo_url = url_for('static', filename='images/college_logo.png', _external=True)
    return render_template(
        'emails/student_welcome.html',
        student_name="Rahul Sharma",
        username="2026BCA1042",
        password="Pass@2026",
        roll_number="42",
        enrollment_number="2026BCA1042",
        division="A",
        login_url=url_for('home', _external=True),
        logo_url=logo_url
    )


# --- Email Delivery Tracking & Retry Routes ---
@admin_bp.route('/admin/email-logs', endpoint='email_logs')
def email_logs():
    """Renders Admin Email Delivery Logs page."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    from services.email_service import get_all_email_logs
    logs = get_all_email_logs()
    return render_template('admin/email_logs.html', logs=logs)


@admin_bp.route('/admin/email-logs/retry/<int:log_id>', methods=['POST'], endpoint='retry_email')
def retry_email(log_id):
    """Retries sending a single FAILED welcome email in the background."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    from services.email_service import retry_single_email
    success, message, status_code = retry_single_email(log_id)
    return jsonify({"success": success, "message": message}), status_code


@admin_bp.route('/admin/email-logs/bulk-retry', methods=['POST'], endpoint='bulk_retry_emails')
def bulk_retry_emails():
    """Retries ALL FAILED emails in the background."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    from services.email_service import retry_all_failed_emails
    success, message, status_code = retry_all_failed_emails()
    return jsonify({"success": success, "message": message}), status_code


# --- Admin Results & Internal Marks Management Routes ---
@admin_bp.route('/admin/results', endpoint='admin_results')
def admin_results():
    """
    Renders the main Admin Results & Internal Marks Portal.
    """
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))
    
    from services.subject_service import get_all_subjects
    from services.academic_service import get_academic_settings
    from services.result_declaration_service import get_declaration_status, get_semester_faculty_submission_status
    
    academic_settings = get_academic_settings()
    subjects = get_all_subjects()
    
    # Selected filters from query string or defaults
    selected_year = request.args.get('academic_year', academic_settings.academic_year if academic_settings else '2026-27').strip()
    try:
        selected_sem = int(request.args.get('semester', 1))
    except (ValueError, TypeError):
        selected_sem = 1
    selected_div = request.args.get('division', 'A').strip().upper()
    try:
        selected_subject_id = int(request.args.get('subject_id', 0))
    except (ValueError, TypeError):
        selected_subject_id = 0

    decl_info = get_declaration_status(selected_year, selected_sem, selected_div)
    submission_status = get_semester_faculty_submission_status(selected_year, selected_sem, 'All')

    return render_template(
        'admin/results.html',
        active_page='results',
        academic_settings=academic_settings,
        subjects=subjects,
        selected_year=selected_year,
        selected_sem=selected_sem,
        selected_div=selected_div,
        selected_subject_id=selected_subject_id,
        is_result_declared=decl_info['is_declared'],
        decl_info=decl_info,
        submission_status=submission_status
    )


@admin_bp.route('/admin/results/toggle-declaration', methods=['POST'], endpoint='toggle_result_declaration')
def admin_toggle_result_declaration():
    """Toggles result declaration/publication status for a given semester & academic year."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    try:
        data = request.get_json() or {}
        academic_year = data.get('academic_year')
        semester = data.get('semester')
        division = data.get('division', 'All')

        from services.result_declaration_service import toggle_result_declaration
        decl, is_declared, msg = toggle_result_declaration(
            academic_year=academic_year,
            semester=semester,
            division=division,
            admin_id=session.get('admin_id')
        )

        if not decl:
            return jsonify({"success": False, "message": msg}), 400

        return jsonify({
            "success": True,
            "is_declared": is_declared,
            "message": msg,
            "declared_at": decl.declared_at.strftime('%d-%m-%Y %H:%M') if decl.declared_at else None
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500


@admin_bp.route('/admin/api/results/declaration-status', methods=['GET'], endpoint='api_result_declaration_status')
def api_result_declaration_status():
    """API endpoint to check if result is declared for semester/division."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    try:
        academic_year = request.args.get('academic_year', '2026-27').strip()
        semester = request.args.get('semester', type=int)
        division = request.args.get('division', 'All').strip()

        from services.result_declaration_service import get_declaration_status, get_semester_faculty_submission_status
        status = get_declaration_status(academic_year, semester, division)
        sub_status = get_semester_faculty_submission_status(academic_year, semester, 'All')
        return jsonify({
            "success": True,
            "is_declared": status['is_declared'],
            "declared_at": status['declared_at'].strftime('%d-%m-%Y %H:%M') if status['declared_at'] else None,
            "submission_status": sub_status
        }), 200
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@admin_bp.route('/admin/api/results/subject', methods=['GET'], endpoint='api_admin_subject_results')
def api_admin_subject_results():
    """API endpoint returning subject-wise student internal marks matrix."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    try:
        subject_id = request.args.get('subject_id', type=int)
        division = request.args.get('division', 'A').strip().upper()
        semester = request.args.get('semester', type=int)
        academic_year = request.args.get('academic_year', '2026-27').strip()

        if not subject_id:
            return jsonify({"success": False, "message": "Subject ID is required"}), 400

        from services.admin_results_service import get_admin_subject_marks_matrix
        matrix_data, err = get_admin_subject_marks_matrix(
            subject_id=subject_id,
            division=division,
            semester=semester,
            academic_year=academic_year
        )

        if err:
            return jsonify({"success": False, "message": err}), 400

        return jsonify({"success": True, "data": matrix_data}), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500


@admin_bp.route('/admin/api/results/class-matrix', methods=['GET'], endpoint='api_admin_class_matrix')
def api_admin_class_matrix():
    """API endpoint returning multi-subject internal marks matrix for all active subjects in a class."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    try:
        division = request.args.get('division', 'A').strip().upper()
        semester = request.args.get('semester', 1, type=int)
        academic_year = request.args.get('academic_year', '2026-27').strip()

        from services.admin_results_service import get_admin_class_results_matrix
        matrix_data, err = get_admin_class_results_matrix(
            semester=semester,
            division=division,
            academic_year=academic_year
        )

        if err:
            return jsonify({"success": False, "message": err}), 400

        return jsonify({"success": True, "data": matrix_data}), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500


@admin_bp.route('/admin/api/results/student/<int:student_id>', methods=['GET'], endpoint='api_admin_student_result')
def api_admin_student_result(student_id):
    """API endpoint returning full student result card for Quick View Modal."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    try:
        semester = request.args.get('semester', type=int)
        academic_year = request.args.get('academic_year', '2026-27').strip()

        from services.admin_results_service import get_admin_student_full_result
        result_data, err = get_admin_student_full_result(
            student_id=student_id,
            semester=semester,
            academic_year=academic_year
        )

        if err:
            return jsonify({"success": False, "message": err}), 400

        return jsonify({"success": True, "data": result_data}), 200
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500


@admin_bp.route('/admin/results/export/excel', endpoint='admin_results_export_excel')
def admin_results_export_excel():
    """Generates Excel download of internal marks for admin."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    try:
        subject_id = request.args.get('subject_id', type=int)
        division = request.args.get('division', 'A').strip().upper()
        semester = request.args.get('semester', type=int)
        academic_year = request.args.get('academic_year', '2026-27').strip()

        if not subject_id:
            flash("Please select a subject to export.", "warning")
            return redirect(url_for('admin_results'))

        from services.admin_results_service import get_admin_subject_marks_matrix
        from services.export_marks_service import generate_internal_marks_excel, sanitize_filename_component
        from flask import send_file

        matrix_data, err = get_admin_subject_marks_matrix(
            subject_id=subject_id,
            division=division,
            semester=semester,
            academic_year=academic_year
        )
        if err or not matrix_data:
            flash(f"Export Error: {err}", "danger")
            return redirect(url_for('admin_results'))

        faculty_name = matrix_data.get("faculty", {}).get("faculty_name", "Admin Office")
        buffer = generate_internal_marks_excel(matrix_data, faculty_name=faculty_name)

        sub_code = sanitize_filename_component(matrix_data["subject"]["subject_code"])
        div_clean = sanitize_filename_component(division)
        filename = f"Internal_Marks_{sub_code}_Div_{div_clean}_Sem_{semester}.xlsx"

        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        flash(f"Excel Export Error: {str(e)}", "danger")
        return redirect(url_for('admin_results'))


@admin_bp.route('/admin/results/export/pdf', endpoint='admin_results_export_pdf')
def admin_results_export_pdf():
    """Generates PDF download of internal marks for admin."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    try:
        subject_id = request.args.get('subject_id', type=int)
        division = request.args.get('division', 'A').strip().upper()
        semester = request.args.get('semester', type=int)
        academic_year = request.args.get('academic_year', '2026-27').strip()

        if not subject_id:
            flash("Please select a subject to export.", "warning")
            return redirect(url_for('admin_results'))

        from services.admin_results_service import get_admin_subject_marks_matrix
        from services.export_marks_service import generate_internal_marks_pdf, sanitize_filename_component
        from flask import send_file

        matrix_data, err = get_admin_subject_marks_matrix(
            subject_id=subject_id,
            division=division,
            semester=semester,
            academic_year=academic_year
        )
        if err or not matrix_data:
            flash(f"Export Error: {err}", "danger")
            return redirect(url_for('admin_results'))

        faculty_name = matrix_data.get("faculty", {}).get("faculty_name", "Admin Office")
        buffer = generate_internal_marks_pdf(matrix_data, faculty_name=faculty_name)

        sub_code = sanitize_filename_component(matrix_data["subject"]["subject_code"])
        div_clean = sanitize_filename_component(division)
        filename = f"Internal_Marks_{sub_code}_Div_{div_clean}_Sem_{semester}.pdf"

        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype="application/pdf"
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        flash(f"PDF Export Error: {str(e)}", "danger")
        return redirect(url_for('admin_results'))


@admin_bp.route('/admin/results/student/<int:student_id>/pdf', endpoint='admin_student_result_pdf')
def admin_student_result_pdf(student_id):
    """Generates official single-student Internal Assessment Result PDF for download."""
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    try:
        semester = request.args.get('semester', type=int)
        academic_year = request.args.get('academic_year', '2026-27').strip()

        from services.admin_results_service import get_admin_student_full_result
        from services.export_marks_service import generate_student_result_pdf, sanitize_filename_component
        from flask import send_file

        result_data, err = get_admin_student_full_result(
            student_id=student_id,
            semester=semester,
            academic_year=academic_year
        )
        if err or not result_data:
            flash(f"Student Result PDF Error: {err}", "danger")
            return redirect(url_for('admin_results'))

        # STRICT SAFEGUARD: Only allow downloading official PDF if result is declared
        from services.result_declaration_service import is_result_declared
        st_div = result_data.get("student", {}).get("division", "All")
        if not is_result_declared(academic_year, semester, st_div):
            flash(f"Cannot download marksheet: Results for Semester {semester} ({academic_year}) have not been officially published yet. All subjects must be final-saved and declared first.", "warning")
            return redirect(url_for('admin_results'))

        buffer = generate_student_result_pdf(result_data)
        st_enroll = sanitize_filename_component(result_data.get("student", {}).get("enrollment_no", "Student"))
        sem_num = result_data.get("semester", 1)
        filename = f"Official_Internal_Result_{st_enroll}_Sem_{sem_num}.pdf"

        return send_file(
            buffer,
            as_attachment=True,
            download_name=filename,
            mimetype="application/pdf"
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        flash(f"PDF Export Error: {str(e)}", "danger")
        return redirect(url_for('admin_results'))


@admin_bp.route('/admin/attendance/blacklist', endpoint='admin_attendance_blacklist')
def admin_attendance_blacklist():
    """Attendance Defaulter / Blacklist Filter & Report page."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    college = get_college_settings()
    current_ay = getattr(college, 'current_academic_year', '2026-27') or '2026-27'
    default_overall = float(college.min_overall_attendance) if (college and getattr(college, 'min_overall_attendance', None) is not None) else 75.0
    default_subject = float(college.min_subject_attendance) if (college and getattr(college, 'min_subject_attendance', None) is not None) else 75.0

    semester = request.args.get('semester', default=5, type=int)
    division = request.args.get('division', default='All', type=str).strip()
    academic_year = request.args.get('academic_year', default=current_ay, type=str).strip()
    overall_threshold = request.args.get('overall_threshold', default=default_overall, type=float)
    subject_threshold = request.args.get('subject_threshold', default=default_subject, type=float)

    from services.attendance_service import get_semester_attendance_blacklist
    blacklist_data = get_semester_attendance_blacklist(
        semester=semester,
        academic_year=academic_year,
        overall_threshold=overall_threshold,
        subject_threshold=subject_threshold,
        division=division
    )

    return render_template(
        'admin/attendance_blacklist.html',
        active_page='attendance_blacklist',
        blacklist_data=blacklist_data,
        selected_semester=semester,
        selected_division=division,
        selected_academic_year=academic_year,
        overall_threshold=overall_threshold,
        subject_threshold=subject_threshold
    )


@admin_bp.route('/admin/attendance/blacklist/pdf', endpoint='admin_attendance_blacklist_pdf')
def admin_attendance_blacklist_pdf():
    """Generates official institutional Attendance Blacklist / Defaulter PDF report."""
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    college = get_college_settings()
    current_ay = getattr(college, 'current_academic_year', '2026-27') or '2026-27'
    default_overall = float(college.min_overall_attendance) if (college and getattr(college, 'min_overall_attendance', None) is not None) else 75.0
    default_subject = float(college.min_subject_attendance) if (college and getattr(college, 'min_subject_attendance', None) is not None) else 75.0

    semester = request.args.get('semester', default=5, type=int)
    division = request.args.get('division', default='All', type=str).strip()
    academic_year = request.args.get('academic_year', default=current_ay, type=str).strip()
    overall_threshold = request.args.get('overall_threshold', default=default_overall, type=float)
    subject_threshold = request.args.get('subject_threshold', default=default_subject, type=float)

    from services.attendance_service import get_semester_attendance_blacklist, generate_attendance_blacklist_pdf
    from flask import send_file

    blacklist_data = get_semester_attendance_blacklist(
        semester=semester,
        academic_year=academic_year,
        overall_threshold=overall_threshold,
        subject_threshold=subject_threshold,
        division=division
    )

    pdf_buffer = generate_attendance_blacklist_pdf(blacklist_data)
    filename = f"Attendance_Defaulter_List_Sem_{semester}_{division}_{academic_year}.pdf"

    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=filename,
        mimetype='application/pdf'
    )


# ==============================================================================
# ADMIN OLD STUDENTS (ARCHIVED STUDENTS) MANAGEMENT & REPORTING
# ==============================================================================

@admin_bp.route('/admin/old-students', endpoint='admin_old_students')
def admin_old_students():
    """
    Renders Admin Old Students section displaying graduated / archived students.
    Strictly queries ONLY the `archived_students` table.
    Provides academic year filtering, search, and report exports.
    """
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    from extensions import db
    from models import ArchivedStudent, ArchivedInternalMark

    # Fetch distinct academic years present in archived_students table ONLY
    year_rows = db.session.query(ArchivedStudent.academic_year).distinct().all()
    available_years = sorted(list(set(r[0] for r in year_rows if r[0])), reverse=True)

    selected_year = request.args.get('academic_year', 'All').strip()
    search_query = request.args.get('q', '').strip()

    # Base query strictly from ArchivedStudent table
    query = ArchivedStudent.query

    if selected_year and selected_year.lower() != 'all':
        query = query.filter(ArchivedStudent.academic_year == selected_year)

    if search_query:
        q_term = f"%{search_query}%"
        query = query.filter(
            (ArchivedStudent.full_name.ilike(q_term)) |
            (ArchivedStudent.enrollment_no.ilike(q_term)) |
            (ArchivedStudent.roll_number.ilike(q_term)) |
            (ArchivedStudent.email.ilike(q_term))
        )

    old_students = query.order_by(ArchivedStudent.academic_year.desc(), db.cast(ArchivedStudent.roll_number, db.Integer).asc()).all()

    # Pre-calculate count of recorded marks per student
    total_old_students = len(old_students)
    total_batches = len(available_years)

    return render_template(
        'admin/old_students.html',
        active_page='old_students',
        students=old_students,
        available_years=available_years,
        selected_year=selected_year,
        search_query=search_query,
        total_students=total_old_students,
        total_batches=total_batches
    )


@admin_bp.route('/admin/old-students/student/<int:student_id>/marks', endpoint='admin_old_student_marks')
def admin_old_student_marks(student_id):
    """
    JSON API returning student profile and all historical semester marks
    strictly from `archived_internal_marks` for modal preview.
    """
    if "admin_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    from models import ArchivedStudent
    from services.alumni_service import get_archived_student_results

    student = ArchivedStudent.query.get(student_id)
    if not student:
        return jsonify({"success": False, "message": "Student record not found"}), 404

    st_dict, semesters_data, summary = get_archived_student_results(student.enrollment_no)

    return jsonify({
        "success": True,
        "student": st_dict,
        "semesters": semesters_data,
        "summary": summary
    })


@admin_bp.route('/admin/old-students/export-excel', endpoint='admin_old_students_export_excel')
def admin_old_students_export_excel():
    """
    Generates and downloads an Excel (.xlsx) report for archived students of the selected year.
    """
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    from services.alumni_service import export_archived_students_excel
    academic_year = request.args.get('academic_year', 'All').strip()

    wb_buffer = export_archived_students_excel(academic_year)
    safe_year = academic_year.replace('-', '_').replace(' ', '_')
    filename = f"Old_Students_Report_{safe_year}.xlsx"

    return send_file(
        wb_buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@admin_bp.route('/admin/old-students/export-pdf', endpoint='admin_old_students_export_pdf')
def admin_old_students_export_pdf():
    """
    Generates and downloads an official PDF report for archived students of the selected year.
    """
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    from services.alumni_service import export_archived_students_pdf
    academic_year = request.args.get('academic_year', 'All').strip()

    pdf_buffer = export_archived_students_pdf(academic_year)
    safe_year = academic_year.replace('-', '_').replace(' ', '_')
    filename = f"Old_Students_Directory_{safe_year}.pdf"

    return send_file(
        pdf_buffer,
        as_attachment=True,
        download_name=filename,
        mimetype="application/pdf"
    )


@admin_bp.route('/admin/old-students/student/<int:student_id>/pdf', endpoint='admin_old_student_pdf')
def admin_old_student_pdf(student_id):
    """
    Generates individual marksheet PDF for an archived student (single semester or consolidated).
    """
    if "admin_id" not in session:
        return redirect(url_for('admin_login'))

    from models import ArchivedStudent
    from services.alumni_service import generate_archived_student_consolidated_pdf, generate_archived_student_semester_pdf

    student = ArchivedStudent.query.get_or_404(student_id)
    semester = request.args.get('semester', type=int)

    try:
        if semester:
            pdf_buffer = generate_archived_student_semester_pdf(student.enrollment_no, semester)
            filename = f"Marksheet_Sem_{semester}_{student.enrollment_no}.pdf"
        else:
            pdf_buffer = generate_archived_student_consolidated_pdf(student.enrollment_no)
            filename = f"Consolidated_Marksheet_{student.enrollment_no}.pdf"

        return send_file(
            pdf_buffer,
            as_attachment=True,
            download_name=filename,
            mimetype="application/pdf"
        )
    except Exception as e:
        flash(f"Could not generate PDF: {str(e)}", "danger")
        return redirect(url_for('admin.admin_old_students'))


# ==============================================================================
# Announcement & Notification Management Routes
# ==============================================================================

@admin_bp.route('/admin/notices', methods=['GET'], endpoint='admin_notices')
@admin_required
def admin_notices():
    """Renders the Announcement and Notification Management console."""
    from services.notification_service import get_all_admin_notices, mark_all_notifications_as_read
    from services.academic_service import get_academic_settings, get_active_semesters

    if "admin_id" in session:
        try:
            mark_all_notifications_as_read('Admin', session['admin_id'])
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
        current_app.logger.warning(f"[Admin Notices Academic Error] {acad_err}")

    notices = []
    try:
        notices = get_all_admin_notices(filter_by_cycle=True)
    except Exception as notif_err:
        from extensions import db
        db.session.rollback()
        current_app.logger.error(f"[Admin Notices Query Error] {notif_err}")
        notices = []

    return render_template(
        'admin/notices.html',
        notices=notices,
        active_semesters=active_semesters,
        semester_cycle=cycle,
        active_page='notices'
    )


@admin_bp.route('/admin/notices/create', methods=['POST'], endpoint='admin_create_notice')
@admin_required
def admin_create_notice():
    """Handles creating a new broadcast notification by Admin."""
    from services.notification_service import create_notification, save_notification_file

    title = request.form.get('title', '').strip()
    message = request.form.get('message', '').strip()
    category = request.form.get('category', 'General').strip()
    priority = request.form.get('priority', 'Normal').strip()
    target_audience = request.form.get('target_audience', 'All').strip()
    start_date = request.form.get('start_date', None)
    end_date = request.form.get('end_date', None)

    if not title or not message:
        flash('Title and Announcement message are required.', 'danger')
        return redirect(url_for('admin_notices'))

    # Handle optional photo / circular attachment upload
    try:
        file = request.files.get('attachment')
        photo_file, file_type = save_notification_file(file)

        # If target audience is Student, handle selected semesters
        if target_audience == 'Student':
            selected_sems = request.form.getlist('target_semesters')
            # If 'All' is in selected or no specific sem selected, broadcast to all semesters
            if not selected_sems or 'All' in selected_sems:
                create_notification(
                    title=title,
                    message=message,
                    category=category,
                    posted_by_role='Admin',
                    admin_id=session.get('admin_id'),
                    target_audience='Student',
                    target_semester=None,
                    target_division='All',
                    start_date=start_date,
                    end_date=end_date,
                    priority=priority,
                    photo_file=photo_file,
                    file_type=file_type
                )
            else:
                for sem_str in selected_sems:
                    try:
                        sem_num = int(sem_str)
                        create_notification(
                            title=title,
                            message=message,
                            category=category,
                            posted_by_role='Admin',
                            admin_id=session.get('admin_id'),
                            target_audience='Student',
                            target_semester=sem_num,
                            target_division='All',
                            start_date=start_date,
                            end_date=end_date,
                            priority=priority,
                            photo_file=photo_file,
                            file_type=file_type
                        )
                    except ValueError:
                        continue
        else:
            # For 'All', 'Guest', 'Faculty'
            create_notification(
                title=title,
                message=message,
                category=category,
                posted_by_role='Admin',
                admin_id=session.get('admin_id'),
                target_audience=target_audience,
                target_semester=None,
                target_division='All',
                start_date=start_date,
                end_date=end_date,
                priority=priority,
                photo_file=photo_file,
                file_type=file_type
            )

        flash(f"Notification broadcast successfully to {target_audience}!", 'success')
    except Exception as e:
        from extensions import db
        db.session.rollback()
        current_app.logger.error(f"[admin_create_notice Error] {e}", exc_info=True)
        flash(f"Failed to publish notification: {str(e)}", 'danger')

    return redirect(url_for('admin_notices'))


@admin_bp.route('/admin/notices/toggle/<int:notice_id>', methods=['POST'], endpoint='admin_toggle_notice')
@admin_required
def admin_toggle_notice(notice_id):
    """Toggles active/inactive status of a notification."""
    from services.notification_service import toggle_notification_status
    success, msg = toggle_notification_status(notice_id)
    if success:
        flash(msg, 'success')
    else:
        flash(msg, 'danger')
    return redirect(url_for('admin_notices'))


@admin_bp.route('/admin/notices/delete/<int:notice_id>', methods=['POST'], endpoint='admin_delete_notice')
@admin_required
def admin_delete_notice(notice_id):
    """Deletes an announcement permanently."""
    from services.notification_service import delete_notification
    success, msg = delete_notification(notice_id, 'Admin', session.get('admin_id'))
    if success:
        flash(msg, 'success')
    else:
        flash(msg, 'danger')
    return redirect(url_for('admin_notices'))


# ------------------------------------------------------------------------------
# Campus Gallery Management Routes
# ------------------------------------------------------------------------------
@admin_bp.route('/admin/gallery', methods=['GET', 'POST'], endpoint='admin_gallery')
@admin_required
def admin_gallery():
    """
    Renders Campus Gallery Management page and handles new photo uploads.
    - GET: Displays stats, category filters, and photo cards grid.
    - POST: Uploads photo with title, category, description, and featured flag.
    """
    from services.gallery_service import (
        get_admin_gallery_items, get_gallery_stats, create_gallery_item,
        GALLERY_CATEGORIES
    )

    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        category = request.form.get('category', 'Campus Life')
        description = request.form.get('description', '').strip()
        is_featured = bool(request.form.get('is_featured'))
        file_obj = request.files.get('photo')
        admin_id = session.get('admin_id')

        success, msg, item = create_gallery_item(
            title=title,
            category=category,
            file_obj=file_obj,
            description=description,
            is_featured=is_featured,
            admin_id=admin_id
        )
        if success:
            flash(msg, 'success')
        else:
            flash(msg, 'danger')
        return redirect(url_for('admin_gallery'))

    selected_category = request.args.get('category', 'All').strip()
    search_query = request.args.get('q', '').strip()
    sort_option = request.args.get('sort', 'newest').strip()

    items = get_admin_gallery_items(
        category=selected_category,
        search=search_query,
        sort=sort_option
    )
    stats = get_gallery_stats()

    return render_template(
        'admin/gallery.html',
        items=items,
        stats=stats,
        categories=GALLERY_CATEGORIES,
        selected_category=selected_category,
        search_query=search_query,
        sort_option=sort_option,
        active_page='gallery'
    )


@admin_bp.route('/admin/gallery/edit/<int:item_id>', methods=['POST'], endpoint='admin_gallery_edit')
@admin_required
def admin_gallery_edit(item_id):
    """Updates title, category, description, or photo for an existing gallery item."""
    from services.gallery_service import update_gallery_item
    title = request.form.get('title', '').strip()
    category = request.form.get('category', 'Campus Life')
    description = request.form.get('description', '').strip()
    is_featured = bool(request.form.get('is_featured'))
    status = request.form.get('status', 'Active')
    file_obj = request.files.get('photo')

    success, msg = update_gallery_item(
        item_id=item_id,
        title=title,
        category=category,
        description=description,
        is_featured=is_featured,
        status=status,
        file_obj=file_obj
    )
    if success:
        flash(msg, 'success')
    else:
        flash(msg, 'danger')
    return redirect(url_for('admin_gallery'))


@admin_bp.route('/admin/gallery/delete/<int:item_id>', methods=['POST'], endpoint='admin_gallery_delete')
@admin_required
def admin_gallery_delete(item_id):
    """Deletes a gallery photo permanently from database and disk."""
    from services.gallery_service import delete_gallery_item
    success, msg = delete_gallery_item(item_id)
    if success:
        flash(msg, 'success')
    else:
        flash(msg, 'danger')
    return redirect(url_for('admin_gallery'))


@admin_bp.route('/admin/gallery/toggle/<int:item_id>', methods=['POST'], endpoint='admin_gallery_toggle')
@admin_required
def admin_gallery_toggle(item_id):
    """Toggles photo visibility between Active and Inactive."""
    from services.gallery_service import toggle_gallery_item_status
    success, msg, new_status = toggle_gallery_item_status(item_id)
    if request.is_json:
        return {"success": success, "message": msg, "status": new_status}
    if success:
        flash(msg, 'success')
    else:
        flash(msg, 'danger')
    return redirect(url_for('admin_gallery'))


@admin_bp.route('/admin/gallery/featured/<int:item_id>', methods=['POST'], endpoint='admin_gallery_featured')
@admin_required
def admin_gallery_featured(item_id):
    """Toggles featured badge status for homepage showcase."""
    from services.gallery_service import toggle_gallery_item_featured
    success, msg, is_featured = toggle_gallery_item_featured(item_id)
    if request.is_json:
        return {"success": success, "message": msg, "is_featured": is_featured}
    if success:
        flash(msg, 'success')
    else:
        flash(msg, 'danger')
    return redirect(url_for('admin_gallery'))


@admin_bp.route('/admin/website/homepage', methods=['GET', 'POST'], endpoint='admin_homepage_content')
@admin_required
def admin_homepage_content():
    """
    Renders and updates Public Home Page Content Management.
    Controls Hero Banner, Live Stats Counters, News Ticker, and Feature Highlights.
    """
    from services.homepage_service import get_homepage_settings, update_homepage_settings

    if request.method == 'POST':
        file_obj = request.files.get('banner_image')
        success, msg = update_homepage_settings(request.form, file_obj)
        if success:
            flash(msg, 'success')
        else:
            flash(msg, 'danger')
        return redirect(url_for('admin_homepage_content'))

    settings = get_homepage_settings()
    return render_template(
        'admin/homepage_content.html',
        settings=settings,
        active_page='website'
    )


# ------------------------------------------------------------------------------
# Student Device Management Console & Instant Reminders
# ------------------------------------------------------------------------------
@admin_bp.route('/admin/device-management', endpoint='admin_device_management')
@admin_bp.route('/admin/device_management', endpoint='admin_device_management_alias')
@admin_required
def admin_device_management():
    """
    Renders the Admin Student Device Management Console.
    Allows filtering by Semester, Division, and Device Binding Status.
    Enables 1-click device reset and targeted reminder alerts for attendance passkey registration.
    """
    from services.academic_service import get_academic_settings, get_active_semesters

    academic = get_academic_settings()
    cycle = academic.semester_cycle if academic else 'Odd'
    active_semesters = get_active_semesters(cycle)

    return render_template(
        'admin/device_management.html',
        academic=academic,
        active_semesters=active_semesters,
        semester_cycle=cycle,
        active_page='device_management'
    )


@admin_bp.route('/admin/api/device-management/data', methods=['GET'], endpoint='admin_api_device_data')
@admin_bp.route('/admin/api/device_management/data', methods=['GET'], endpoint='admin_api_device_data_alias')
@admin_required
def admin_api_device_data():
    """
    API endpoint returning filtered student list and real-time binding statistics.
    Filters: semester (int/all), division (str/all), status (all/bound/unbound), search (str).
    """
    from models import Student

    semester = request.args.get('semester', 'all').strip()
    division = request.args.get('division', 'all').strip()
    status_filter = request.args.get('status', 'all').strip().lower()
    search = request.args.get('search', '').strip().lower()

    query = Student.query.filter_by(status='Active')

    if semester and semester != 'all':
        try:
            query = query.filter_by(semester=int(semester))
        except ValueError:
            pass

    if division and division != 'all':
        query = query.filter_by(division=division)

    students = query.order_by(Student.semester.asc(), Student.division.asc(), db.cast(Student.roll_number, db.Integer).asc()).all()

    total_count = 0
    bound_count = 0
    unbound_count = 0
    student_rows = []

    for s in students:
        is_bound = bool(s.device_fingerprint and not s.device_fingerprint.startswith('PIN-'))

        total_count += 1
        if is_bound:
            bound_count += 1
        else:
            unbound_count += 1

        # Apply status filter
        if status_filter == 'bound' and not is_bound:
            continue
        if status_filter == 'unbound' and is_bound:
            continue

        # Apply search filter
        if search:
            match_name = search in (s.full_name or '').lower()
            match_roll = search in (s.roll_number or '').lower()
            match_enr = search in (s.enrollment_no or '').lower()
            if not (match_name or match_roll or match_enr):
                continue

        bound_at_str = s.device_bound_at.strftime('%d %b %Y, %I:%M %p') if s.device_bound_at else None

        student_rows.append({
            "id": s.id,
            "roll_number": s.roll_number,
            "enrollment_no": s.enrollment_no,
            "full_name": s.full_name,
            "course": s.course or 'BCA',
            "semester": s.semester,
            "division": s.division or 'A',
            "email": s.email,
            "mobile": s.mobile,
            "profile_photo": s.profile_photo or 'default-avatar.png',
            "is_bound": is_bound,
            "device_model": s.device_model or ('Active Device' if is_bound else 'Not Registered'),
            "device_bound_at": bound_at_str,
            "device_reset_allowed": bool(getattr(s, 'device_reset_allowed', 0))
        })

    bound_percent = round((bound_count / total_count * 100), 1) if total_count > 0 else 0
    unbound_percent = round((unbound_count / total_count * 100), 1) if total_count > 0 else 0

    return jsonify({
        "success": True,
        "stats": {
            "total_students": total_count,
            "bound_count": bound_count,
            "bound_percent": bound_percent,
            "unbound_count": unbound_count,
            "unbound_percent": unbound_percent
        },
        "students": student_rows,
        "filtered_count": len(student_rows)
    })


@admin_bp.route('/admin/api/device-management/reset/<int:student_id>', methods=['POST'], endpoint='admin_api_reset_device')
@admin_bp.route('/admin/api/device_management/reset/<int:student_id>', methods=['POST'], endpoint='admin_api_reset_device_alias')
@admin_required
def admin_api_reset_device(student_id):
    """
    1-Click reset of student device registration.
    Clears fingerprint and binds allowed flag so student can register new device.
    """
    from models import Student

    student = Student.query.get(student_id)
    if not student:
        return jsonify({"success": False, "message": "Student not found."}), 404

    student.device_fingerprint = None
    student.device_model = None
    student.device_bound_at = None
    student.device_reset_allowed = 1

    try:
        db.session.commit()
        return jsonify({
            "success": True,
            "message": f"Device registration for {student.full_name} (Roll #{student.roll_number}) has been reset successfully. The student can now register their new device."
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Failed to reset device: {str(e)}"}), 500


@admin_bp.route('/admin/api/device-management/reset-all', methods=['GET', 'POST'], endpoint='admin_api_reset_all_devices')
@admin_bp.route('/admin/api/device_management/reset-all', methods=['GET', 'POST'], endpoint='admin_api_reset_all_devices_alias')
@admin_required
def admin_api_reset_all_devices():
    """
    1-Click reset of all student device registrations.
    Clears device_fingerprint for all students so everyone can re-verify fresh.
    """
    from models import Student
    try:
        updated = Student.query.filter(Student.device_fingerprint.isnot(None)).update({
            'device_fingerprint': None,
            'device_model': None,
            'device_bound_at': None,
            'device_reset_allowed': 1
        }, synchronize_session=False)
        db.session.commit()
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": True, "message": f"Successfully cleared device verification for {updated} students. All students can now re-verify."})
        flash(f"Successfully cleared device verification for {updated} students. All students can now re-verify.", "success")
        return redirect(url_for('admin_device_management'))
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Failed to reset devices: {str(e)}"}), 500


@admin_bp.route('/admin/api/device-management/notify-pending', methods=['POST'], endpoint='admin_api_notify_pending')
@admin_bp.route('/admin/api/device_management/notify-pending', methods=['POST'], endpoint='admin_api_notify_pending_alias')
@admin_required
def admin_api_notify_pending():
    """
    Sends device registration reminder notifications.
    Supports single student alert or bulk reminder to all unbound students.
    """
    from models import Student
    from services.notification_service import create_notification

    try:
        data = request.get_json(silent=True) or request.form.to_dict() or {}
        student_id = data.get('student_id')
        is_bulk = data.get('bulk', False)

        admin_id = session.get('admin_id')

        if student_id and not is_bulk:
            student = Student.query.get(student_id)
            if not student:
                return jsonify({"success": False, "message": "Student not found."}), 404

            title = "[Urgent Alert] Device Registration Required for Attendance"
            msg = (
                f"Dear {student.full_name} (Roll #{student.roll_number}),\n\n"
                f"Your mobile phone has not yet been registered for QR lecture attendance. "
                f"Please log in to your Student Portal, navigate to the 'Scan QR' page, and complete your device verification before today's lectures.\n\n"
                f"Note: In accordance with college policy, attendance cannot be marked via QR code without device registration."
            )

            notif = create_notification(
                title=title,
                message=msg,
                category='Device Binding',
                posted_by_role='Admin',
                admin_id=admin_id,
                target_audience='Student',
                target_semester=student.semester,
                target_division=student.division,
                target_student_id=student.id,
                priority='Urgent'
            )

            if not notif:
                return jsonify({"success": False, "message": "Failed to record reminder in database."}), 500

            return jsonify({
                "success": True,
                "message": f"Reminder alert successfully sent to {student.full_name}!"
            })

        # Bulk Notification
        semester = data.get('semester')
        division = data.get('division')

        query = Student.query.filter_by(status='Active')
        if semester and str(semester).lower() != 'all':
            try:
                query = query.filter_by(semester=int(semester))
            except ValueError:
                pass

        if division and str(division).lower() != 'all':
            query = query.filter_by(division=str(division))

        all_students = query.all()
        unbound_students = [
            s for s in all_students
            if not s.device_fingerprint or s.device_fingerprint.startswith('PIN-')
        ]

        count = len(unbound_students)
        if count == 0:
            return jsonify({
                "success": True,
                "notified_count": 0,
                "message": "All students in the selected filter already have their devices registered! No pending students found."
            })

        target_sem = int(semester) if semester and str(semester).lower() != 'all' else None
        target_div = str(division) if division and str(division).lower() != 'all' else 'All'

        title = "[Urgent Broadcast] Device Registration Required for Attendance"
        msg = (
            "Dear Student,\n\n"
            "Your mobile phone has not yet been registered for QR lecture attendance. "
            "Please log in to your Student Portal, navigate to the 'Scan QR' section, and complete your device verification before attending lectures.\n\n"
            "Note: In accordance with college policy, attendance cannot be marked via QR code without device registration."
        )

        notif = create_notification(
            title=title,
            message=msg,
            category='Device Binding',
            posted_by_role='Admin',
            admin_id=admin_id,
            target_audience='Student',
            target_semester=target_sem,
            target_division=target_div,
            priority='Urgent'
        )

        if not notif:
            return jsonify({"success": False, "message": "Failed to broadcast notification due to a database error."}), 500

        return jsonify({
            "success": True,
            "notified_count": count,
            "message": f"Successfully published urgent device registration alert to {count} pending student(s)!"
        })
    except Exception as e:
        from extensions import db
        db.session.rollback()
        current_app.logger.error(f"[admin_api_notify_pending Error] {e}", exc_info=True)
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500








