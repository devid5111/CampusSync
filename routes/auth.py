"""
CampusSync ERP - Authentication & General Routes
===============================================
File: routes/auth.py
"""

from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify, flash
from services.auth_service import authenticate_admin

auth_bp = Blueprint('auth', __name__)

# --- Home Route ---
@auth_bp.route('/', endpoint='home')
def home():
    """Renders the main homepage (index.html) with active campus notices, featured gallery photos, and dynamic CMS settings."""
    from services.notification_service import get_public_notices
    from services.gallery_service import get_active_gallery_items
    from services.homepage_service import get_homepage_settings
    public_notices = []
    gallery_photos = []
    home_settings = None
    try:
        home_settings = get_homepage_settings()
    except Exception as e:
        print(f"Homepage settings error: {e}")
    try:
        public_notices = get_public_notices(limit=8)
    except Exception as e:
        print(f"Notice fetch error on homepage: {e}")
    try:
        gallery_photos = get_active_gallery_items(limit=6)
    except Exception as e:
        print(f"Gallery fetch error on homepage: {e}")
    return render_template(
        'index.html',
        public_notices=public_notices,
        gallery_photos=gallery_photos,
        home_settings=home_settings
    )

# --- Public Pages Demo Routes (Frontend-only) ---
@auth_bp.route('/about', endpoint='about')
def about():
    """Renders About Us public demo page"""
    return render_template('public/about.html')

@auth_bp.route('/courses', endpoint='courses')
def courses():
    """Renders Academic Programs & Courses public demo page"""
    return render_template('public/courses.html')

@auth_bp.route('/facilities', endpoint='facilities')
def facilities():
    """Renders Campus Facilities public demo page"""
    return render_template('public/facilities.html')

@auth_bp.route('/gallery', endpoint='gallery')
def gallery():
    """Renders Campus Photo Gallery public page with category filtering, instant search, and full-resolution lightbox viewer."""
    from services.gallery_service import get_active_gallery_items, GALLERY_CATEGORIES
    selected_category = request.args.get('category', 'All').strip()
    # Always fetch all active photos so client-side filter pills, search, and sorting work seamlessly across all categories
    photos = get_active_gallery_items()
    return render_template(
        'public/gallery.html',
        photos=photos,
        categories=GALLERY_CATEGORIES,
        selected_category=selected_category
    )

@auth_bp.route('/gallery/view/<int:item_id>', methods=['POST'], endpoint='gallery_view')
def gallery_view(item_id):
    """Increments view counter for a gallery photograph."""
    from services.gallery_service import increment_gallery_view
    new_views = increment_gallery_view(item_id)
    return {"success": True, "views_count": new_views}

@auth_bp.route('/contact', endpoint='contact')
def contact():
    """Renders Contact Us & Inquiry public demo page"""
    return render_template('public/contact.html')

# --- Admin Login Route ---
@auth_bp.route('/admin/login', methods=['GET', 'POST'], endpoint='admin_login')
@auth_bp.route('/auth/admin-login', methods=['GET', 'POST'])
def admin_login():
    """
    Handles Admin Login logic.
    - GET: Displays login form.
    - POST: Verifies credentials, creates session.
    """
    if "admin_id" in session:
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        username = request.form.get('admin_username')
        password = request.form.get('admin_password')

        admin = authenticate_admin(username, password)
        if admin:
            session.permanent = True
            session["admin_id"] = admin.id
            session["user_role"] = "admin"
            session["last_active"] = datetime.utcnow().timestamp()
            return redirect(url_for('admin_dashboard'))
        else:
            return render_template('auth/admin-login.html', error="Invalid Username or Password")

    success_msg = request.args.get('success')
    return render_template('auth/admin-login.html', success=success_msg)

# --- Admin Logout Route ---
@auth_bp.route('/admin/logout', endpoint='admin_logout')
def admin_logout():
    """
    Handles Admin Logout.
    - Clears Admin session keys.
    - Redirects user back to Admin Login page.
    """
    session.pop("admin_id", None)
    if session.get("user_role") == "admin":
        session.pop("user_role", None)
    return redirect(url_for('admin_login'))

# --- Admin Forgot Password Route ---
@auth_bp.route('/admin/forgot-password', methods=['GET', 'POST'], endpoint='admin_forgot_password')
def admin_forgot_password():
    """
    Handles Admin Password Reset OTP request:
    - GET: Shows forgot password email form.
    - POST: Generates hashed OTP in DB and sends OTP email.
    """
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        from services.password_reset_service import request_password_reset_otp
        success, msg, target_email = request_password_reset_otp('admin', email)

        if success:
            return redirect(url_for('admin_reset_password', email=target_email))
        else:
            return render_template(
                'auth/forgot-password.html',
                portal_name='Admin',
                action_url=url_for('admin_forgot_password'),
                login_url=url_for('admin_login'),
                email=email,
                error=msg
            )

    preset_email = request.args.get('email', '')
    return render_template(
        'auth/forgot-password.html',
        portal_name='Admin',
        action_url=url_for('admin_forgot_password'),
        login_url=url_for('admin_login'),
        email=preset_email
    )

# --- Admin Reset Password Route ---
@auth_bp.route('/admin/reset-password', methods=['GET', 'POST'], endpoint='admin_reset_password')
def admin_reset_password():
    """
    Handles Admin OTP verification and password update:
    - GET: Shows reset password form.
    - POST: Verifies hashed OTP from DB, updates password, redirects to login.
    """
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        otp = request.form.get('otp', '').strip()
        new_pwd = request.form.get('new_password', '')
        confirm_pwd = request.form.get('confirm_password', '')

        from services.password_reset_service import verify_otp_and_reset_password
        success, msg = verify_otp_and_reset_password('admin', email, otp, new_pwd, confirm_pwd)

        if success:
            return redirect(url_for('admin_login', success=msg))
        else:
            return render_template(
                'auth/reset-password.html',
                portal_name='Admin',
                action_url=url_for('admin_reset_password'),
                resend_url=url_for('admin_forgot_password', email=email),
                login_url=url_for('admin_login'),
                email=email,
                error=msg
            )

    email = request.args.get('email', '')
    return render_template(
        'auth/reset-password.html',
        portal_name='Admin',
        action_url=url_for('admin_reset_password'),
        resend_url=url_for('admin_forgot_password', email=email),
        login_url=url_for('admin_login'),
        email=email
    )


# ==============================================================================
# POPUP MODAL JSON API ENDPOINTS (FOR ADMIN, FACULTY & STUDENT POPUPS)
# ==============================================================================

@auth_bp.route('/auth/api/request-otp', methods=['POST'])
def api_request_otp():
    """
    JSON API endpoint for requesting password reset OTP via modal.
    Expects JSON: { "role": "admin"|"faculty"|"student", "email": "..." }
    """
    data = request.get_json(silent=True) or request.form
    role = data.get('role', 'student')
    email = data.get('email', '').strip()

    from services.password_reset_service import request_password_reset_otp
    result = request_password_reset_otp(role, email)
    success, msg, target_email = result[0], result[1], result[2]
    is_blocked = getattr(result, 'blocked', False)
    attempts = getattr(result, 'attempts', 0)

    status_code = 200 if success else (429 if is_blocked else 400)
    return jsonify({
        "success": success,
        "message": msg,
        "email": target_email,
        "blocked": is_blocked,
        "attempts": attempts
    }), status_code


@auth_bp.route('/auth/api/verify-otp', methods=['POST'])
def api_verify_otp():
    """
    JSON API endpoint for validating OTP digits before showing the reset password form.
    Expects JSON: { "role": "admin"|"faculty"|"student", "email": "...", "otp": "..." }
    """
    data = request.get_json(silent=True) or request.form
    role = data.get('role', 'student')
    email = data.get('email', '').strip()
    otp = data.get('otp', '').strip()

    from services.password_reset_service import verify_otp_only
    success, msg = verify_otp_only(role, email, otp)

    return jsonify({
        "success": success,
        "message": msg
    }), (200 if success else 400)


@auth_bp.route('/auth/api/verify-reset-password', methods=['POST'])
def api_verify_reset_password():
    """
    JSON API endpoint for verifying OTP and resetting password via modal.
    Expects JSON: { "role": "admin"|"faculty"|"student", "email": "...", "otp": "...", "new_password": "...", "confirm_password": "..." }
    """
    data = request.get_json(silent=True) or request.form
    role = data.get('role', 'student')
    email = data.get('email', '').strip()
    otp = data.get('otp', '').strip()
    new_pwd = data.get('new_password', '')
    confirm_pwd = data.get('confirm_password', '')

    from services.password_reset_service import verify_otp_and_reset_password
    success, msg = verify_otp_and_reset_password(role, email, otp, new_pwd, confirm_pwd)

    return jsonify({
        "success": success,
        "message": msg
    }), (200 if success else 400)
