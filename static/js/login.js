/**
 * CampusSync - Admin Login Page JavaScript
 * Version: 1.0.0
 * 
 * Features:
 * - Theme Switcher (Dark / Light Mode with localStorage)
 * - Password Visibility Toggle (Show / Hide)
 * - Form Validation & Submit handling for Flask Backend
 */

document.addEventListener('DOMContentLoaded', () => {
  initThemeSwitcher();
  initPasswordToggle();
  initLoginForm();
  initForgotPassword();
  initClearCredentials();
  initSessionExpiredNotice();
});

/**
 * 1. Theme Switcher Logic (Light / Dark Mode)
 */
function initThemeSwitcher() {
  const themeToggleBtn = document.getElementById('themeToggleBtn');
  const themeIcon = document.getElementById('themeIcon');
  const themeText = document.getElementById('themeText');
  
  if (!themeToggleBtn) return;

  const storedTheme = localStorage.getItem('campussync_theme') || 'light';
  applyTheme(storedTheme);

  themeToggleBtn.addEventListener('click', () => {
    const currentTheme = document.documentElement.getAttribute('data-bs-theme') || 'light';
    const newTheme = currentTheme === 'light' ? 'dark' : 'light';
    applyTheme(newTheme);
    localStorage.setItem('campussync_theme', newTheme);
  });

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-bs-theme', theme);
    
    if (theme === 'dark') {
      if (themeIcon) themeIcon.className = 'bi bi-sun-fill text-warning';
      if (themeText) themeText.textContent = 'Light Mode';
    } else {
      if (themeIcon) themeIcon.className = 'bi bi-moon-stars-fill text-primary';
      if (themeText) themeText.textContent = 'Dark Mode';
    }
  }
}

/**
 * 2. Show / Hide Password Toggle
 * Enables eye toggle for Admin, Faculty, Student, and Modal password fields.
 */
function initPasswordToggle() {
  const toggleButtons = document.querySelectorAll('.btn-toggle-password');

  toggleButtons.forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      
      // Find the password input in the same input-group
      const inputGroup = btn.closest('.input-group');
      let passwordInput = inputGroup ? inputGroup.querySelector('input') : null;
      
      // Fallback to known IDs if input not found in inputGroup
      if (!passwordInput) {
        passwordInput = document.getElementById('adminPassword') || 
                        document.getElementById('facultyPassword') || 
                        document.getElementById('studentPassword');
      }

      if (!passwordInput) return;

      const icon = btn.querySelector('i') || document.getElementById('togglePasswordIcon');
      const isPassword = passwordInput.getAttribute('type') === 'password';

      if (isPassword) {
        passwordInput.setAttribute('type', 'text');
        if (icon) {
          icon.className = 'bi bi-eye-slash';
        }
        btn.setAttribute('title', 'Hide password');
      } else {
        passwordInput.setAttribute('type', 'password');
        if (icon) {
          icon.className = 'bi bi-eye';
        }
        btn.setAttribute('title', 'Show password');
      }
    });
  });
}

/**
 * 3. Login Form Client Validation
 */
function initLoginForm() {
  const loginForms = document.querySelectorAll('#adminLoginForm, #facultyLoginForm');

  loginForms.forEach(form => {
    const loginBtn = form.querySelector('#loginBtn, button[type="submit"]');
    const loginBtnText = form.querySelector('#loginBtnText');
    const loginBtnSpinner = form.querySelector('#loginBtnSpinner');
    const card = form.closest('.login-card') || form.parentElement;
    const alertBox = card ? card.querySelector('#clientErrorAlert') : null;
    const alertMsg = card ? card.querySelector('#clientErrorMessage') : null;

    form.addEventListener('submit', (e) => {
      if (alertBox) alertBox.classList.add('d-none');

      const inputs = form.querySelectorAll('input[type="text"], input[type="email"], input[type="password"]');
      let hasEmpty = false;

      inputs.forEach(input => {
        if (input.hasAttribute('required') && !input.value.trim()) {
          hasEmpty = true;
          input.classList.add('is-invalid');
        } else {
          input.classList.remove('is-invalid');
        }
      });

      if (hasEmpty) {
        e.preventDefault();
        if (alertBox && alertMsg) {
          alertMsg.textContent = 'Please fill in all required fields to continue.';
          alertBox.classList.remove('d-none');
        }
        return;
      }

      // Show loading state on button on submit
      if (loginBtnSpinner) loginBtnSpinner.classList.remove('d-none');
      if (loginBtnText) loginBtnText.textContent = 'Authenticating...';
    });
  });
}

/**
 * 4. Forgot Password UI Link Handler
 */
function initForgotPassword() {
  const forgotPasswordLink = document.getElementById('forgotPasswordLink');
  if (!forgotPasswordLink) return;

  if (forgotPasswordLink.getAttribute('data-bs-toggle') === 'modal') {
    return; // Handled by Bootstrap Modal Popup
  }

  const href = forgotPasswordLink.getAttribute('href');
  if (href && href !== '#' && !href.startsWith('javascript:')) {
    return; // Allow direct link navigation to forgot password route
  }

  forgotPasswordLink.addEventListener('click', (e) => {
    e.preventDefault();
    if (typeof showFlash === 'function') {
      showFlash('info', 'Admin Support', 'Please contact your System Administrator to reset your security credentials.');
    } else {
      let feedback = document.getElementById('feedbackAlert');
      if (!feedback) {
        const card = document.querySelector('.login-card');
        feedback = document.createElement('div');
        feedback.id = 'feedbackAlert';
        if (card) card.insertBefore(feedback, card.firstChild);
      }
      if (feedback) {
        feedback.className = 'alert alert-info alert-dismissible fade show my-2';
        feedback.innerHTML = `<i class="bi bi-info-circle-fill me-2"></i><strong>Admin Support:</strong> Please contact your System Administrator to reset your security credentials.<button type="button" class="btn-close" data-bs-dismiss="alert"></button>`;
      }
    }
  });
}

/**
 * 5. Clear Credentials on Page Load
 */
function initClearCredentials() {
  const loginForm = document.getElementById('adminLoginForm') || 
                    document.getElementById('facultyLoginForm') || 
                    document.getElementById('studentLoginForm');
  if (loginForm) {
    loginForm.setAttribute('autocomplete', 'off');
    // Clear password inputs
    const pwdInputs = loginForm.querySelectorAll('input[type="password"]');
    pwdInputs.forEach(input => {
      input.value = '';
      input.setAttribute('autocomplete', 'new-password');
    });
  }

  // Prevent browser back-cache from restoring form fields
  window.addEventListener('pageshow', (event) => {
    if (event.persisted) {
      if (loginForm) loginForm.reset();
    }
  });
}

/**
 * 6. Session Expired Notification
 */
function initSessionExpiredNotice() {
  const urlParams = new URLSearchParams(window.location.search);
  if (urlParams.get('expired') === '1') {
    const card = document.querySelector('.login-card');
    if (card && !document.getElementById('expiredAlert')) {
      const alertDiv = document.createElement('div');
      alertDiv.id = 'expiredAlert';
      alertDiv.className = 'alert alert-warning alert-dismissible fade show my-2 small';
      alertDiv.role = 'alert';
      alertDiv.innerHTML = '<i class="bi bi-clock-history me-2 fs-6"></i>Your session has expired due to 30 minutes of inactivity. Please sign in again.<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>';
      const formEl = card.querySelector('form');
      if (formEl) {
        card.insertBefore(alertDiv, formEl);
      } else {
        card.prepend(alertDiv);
      }
    }
  }
}
