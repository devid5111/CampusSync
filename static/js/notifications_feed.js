/**
 * CampusSync ERP - Smart Live Notification Bell & Seen Tracking Engine
 * ====================================================================
 * Provides WhatsApp / Instagram style unread counter badge across
 * Admin, Faculty, and Student portals.
 * Automatically polls for new notifications, marks items as read on view,
 * and enables 1-click 'Mark all as read'.
 */

(function () {
    let cachedFeed = [];
    let currentUnreadCount = 0;
    let pollInterval = null;
    let lastToggleTimestamp = 0;

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function getPortalRole() {
        const wrapper = document.querySelector('.notification-dropdown-wrapper');
        if (wrapper && wrapper.getAttribute('data-portal')) {
            return wrapper.getAttribute('data-portal');
        }
        const path = window.location.pathname.toLowerCase();
        if (path.includes('/faculty')) return 'faculty';
        if (path.includes('/student')) return 'student';
        return 'admin';
    }

    async function fetchNotificationsFeed() {
        try {
            const portal = getPortalRole();
            const response = await fetch(`/api/v1/notifications/unread-feed?portal=${encodeURIComponent(portal)}`, {
                headers: { 'Accept': 'application/json' },
                cache: 'no-cache'
            });

            if (!response.ok) return;

            const data = await response.json();
            if (!data.success || !data.authenticated) return;

            const menu = document.querySelector('.notification-dropdown-menu');
            const isMenuOpen = menu && menu.classList.contains('show');

            cachedFeed = data.notifications || [];

            if (isMenuOpen) {
                // If user is currently looking at the open dropdown, keep count 0 and badge hidden
                currentUnreadCount = 0;
                updateBadgeUI(0);
                renderDropdownList(cachedFeed, 0);
            } else {
                currentUnreadCount = data.unread_count || 0;
                updateBadgeUI(currentUnreadCount);
                renderDropdownList(cachedFeed, currentUnreadCount);
            }
        } catch (err) {
            console.warn('CampusSync Notification Feed polling error:', err);
        }
    }

    function updateBadgeUI(count) {
        const badge = document.getElementById('notificationUnreadBadge');
        const dropdownCountBadge = document.getElementById('notificationDropdownCountBadge');
        const bellIcon = document.getElementById('bellIcon');

        if (!badge) return;

        if (count > 0) {
            badge.innerText = count > 99 ? '99+' : count;
            badge.style.display = 'inline-block';
            badge.classList.add('notification-badge-pulse');

            if (dropdownCountBadge) {
                dropdownCountBadge.innerText = `${count} New`;
                dropdownCountBadge.style.display = 'inline-block';
            }

            if (bellIcon) {
                bellIcon.classList.remove('text-secondary');
            }
        } else {
            badge.style.display = 'none';
            badge.innerText = '0';
            badge.classList.remove('notification-badge-pulse');

            if (dropdownCountBadge) {
                dropdownCountBadge.style.display = 'none';
            }
        }
    }

    function renderDropdownList(notifications, unreadCount) {
        const listContainer = document.getElementById('notificationDropdownList');
        if (!listContainer) return;

        if (!notifications || notifications.length === 0) {
            listContainer.innerHTML = `
                <div class="text-center py-4 px-3 text-muted">
                    <div class="mb-2"><i class="bi bi-bell-slash fs-2 text-secondary opacity-50"></i></div>
                    <p class="mb-1 fw-semibold fs-7 text-dark">No Notifications</p>
                    <p class="small text-muted mb-0">You're completely caught up with campus updates!</p>
                </div>
            `;
            return;
        }

        let html = '';
        notifications.forEach((item, index) => {
            const isUnread = !item.is_read;
            const priorityClass = item.priority === 'Urgent' ? 'bg-danger' :
                                 item.priority === 'Important' ? 'bg-warning text-dark' : 'bg-primary';

            const categoryIcon = item.category === 'Exam' ? 'bi-mortarboard' :
                                item.category === 'Test' ? 'bi-file-earmark-text' :
                                item.category === 'Assignment Submit Date' ? 'bi-clock-history' :
                                item.category === 'Holiday' ? 'bi-cup-hot' : 'bi-megaphone';

            html += `
                <div class="notification-item px-3 py-2 border-bottom position-relative ${isUnread ? 'is-unread' : ''}"
                     id="notif-item-${item.id}"
                     data-id="${item.id}"
                     data-index="${index}"
                     onclick="window.handleNotificationItemClick(${item.id}, ${index}, event)">
                    <div class="d-flex align-items-start gap-2">
                        <div class="notif-icon-circle rounded-circle d-flex align-items-center justify-content-center flex-shrink-0 mt-1"
                             style="width: 32px; height: 32px; font-size: 0.85rem; background: ${isUnread ? '#dbeafe' : '#f1f5f9'}; color: ${isUnread ? '#1e40af' : '#475569'};">
                            <i class="bi ${categoryIcon}"></i>
                        </div>
                        <div class="flex-grow-1" style="min-width: 0;">
                            <div class="d-flex align-items-center justify-content-between gap-1 mb-1">
                                <span class="badge ${priorityClass} px-1 py-0 fs-9">${escapeHtml(item.priority)}</span>
                                <small class="text-muted fs-8">${escapeHtml(item.time_ago)}</small>
                            </div>
                            <h6 class="mb-1 text-truncate fs-7 ${isUnread ? 'fw-bold text-dark' : 'text-secondary'}" title="${escapeHtml(item.title)}">
                                ${escapeHtml(item.title)}
                            </h6>
                            <p class="mb-1 text-muted fs-8 lh-sm" style="display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">
                                ${escapeHtml(item.message)}
                            </p>
                            <div class="d-flex align-items-center justify-content-between gap-2 mt-1 fs-9 text-muted">
                                <span><i class="bi bi-person me-1"></i>${escapeHtml(item.posted_by_name || item.posted_by_role)}</span>
                                ${item.photo_file ? '<span class="text-primary"><i class="bi bi-paperclip"></i> Attachment</span>' : ''}
                            </div>
                        </div>
                        ${isUnread ? `
                            <span class="unread-dot rounded-circle bg-primary flex-shrink-0 mt-2"
                                  style="width: 8px; height: 8px; display: inline-block;"
                                  title="Unread"></span>
                        ` : ''}
                    </div>
                </div>
            `;
        });

        listContainer.innerHTML = html;
    }

    // Robust Notification Dropdown Controller
    function openCampusNotificationDropdown() {
        const bellBtn = document.getElementById('notificationBellBtn');
        const menu = document.querySelector('.notification-dropdown-menu');
        if (!menu) return;

        // Dismiss other open dropdown menus on page
        document.querySelectorAll('.dropdown-menu.show').forEach(m => {
            if (m !== menu) m.classList.remove('show');
        });

        menu.classList.add('show');
        if (bellBtn) bellBtn.setAttribute('aria-expanded', 'true');

        // Immediate Unread Counter Clearing when User Views the Bell Menu
        if (currentUnreadCount > 0) {
            currentUnreadCount = 0;
            updateBadgeUI(0);

            // Remove unread dots in the dropdown list visually
            document.querySelectorAll('.notification-item.is-unread').forEach(el => {
                el.classList.remove('is-unread');
                const dot = el.querySelector('.unread-dot');
                if (dot) dot.remove();
            });

            // Mark cached notifications as read locally
            cachedFeed.forEach(n => { n.is_read = true; });

            // Persist to database asynchronously
            const portal = getPortalRole();
            fetch('/api/v1/notifications/mark-read', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ mark_all: true, portal: portal })
            }).catch(err => console.warn('Background mark-read error:', err));
        }
    }

    function closeCampusNotificationDropdown() {
        const bellBtn = document.getElementById('notificationBellBtn');
        const menu = document.querySelector('.notification-dropdown-menu');
        if (!menu) return;

        menu.classList.remove('show');
        if (bellBtn) bellBtn.setAttribute('aria-expanded', 'false');
    }

    function toggleCampusNotificationDropdown(e) {
        if (e) {
            if (e.preventDefault) e.preventDefault();
            if (e.stopPropagation) e.stopPropagation();
        }

        const now = Date.now();
        // Prevent double invocation within 250ms (debounces rapid clicks and touch+click cycles)
        if (now - lastToggleTimestamp < 250) {
            return;
        }
        lastToggleTimestamp = now;

        const menu = document.querySelector('.notification-dropdown-menu');
        if (!menu) return;

        if (menu.classList.contains('show')) {
            closeCampusNotificationDropdown();
        } else {
            openCampusNotificationDropdown();
        }
    }

    window.toggleCampusNotificationDropdown = toggleCampusNotificationDropdown;
    window.openCampusNotificationDropdown = openCampusNotificationDropdown;
    window.closeCampusNotificationDropdown = closeCampusNotificationDropdown;

    // Handles user clicking an individual notification in the dropdown
    window.handleNotificationItemClick = async function (noticeId, index, event) {
        if (event) event.preventDefault();
        closeCampusNotificationDropdown();

        const itemData = cachedFeed.find(n => n.id === noticeId) || (cachedFeed[index] || null);

        // Mark as read immediately on frontend
        const el = document.getElementById(`notif-item-${noticeId}`);
        if (el && el.classList.contains('is-unread')) {
            el.classList.remove('is-unread');
            const dot = el.querySelector('.unread-dot');
            if (dot) dot.remove();

            if (currentUnreadCount > 0) {
                currentUnreadCount -= 1;
                updateBadgeUI(currentUnreadCount);
            }
        }

        // Send API call to mark as read in DB
        try {
            const portal = getPortalRole();
            fetch('/api/v1/notifications/mark-read', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ notification_id: noticeId, portal: portal })
            }).then(r => r.json()).then(data => {
                if (data && data.success && data.unread_count !== undefined) {
                    currentUnreadCount = data.unread_count;
                    updateBadgeUI(currentUnreadCount);
                }
            }).catch(e => console.warn(e));
        } catch (e) {
            console.warn(e);
        }

        // Open Notice Details in Quick Modal
        if (itemData) {
            showNoticeDetailsModal(itemData);
        }
    };

    // 1-Click "Mark all as read"
    window.markAllCampusNotificationsRead = async function (event) {
        if (event) {
            event.preventDefault();
            event.stopPropagation();
        }

        const markBtn = document.getElementById('markAllReadBtn');
        if (markBtn) {
            markBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span> Marking...';
            markBtn.disabled = true;
        }

        // Update frontend immediately
        currentUnreadCount = 0;
        updateBadgeUI(0);
        document.querySelectorAll('.notification-item.is-unread').forEach(el => {
            el.classList.remove('is-unread');
            const dot = el.querySelector('.unread-dot');
            if (dot) dot.remove();
        });

        // API call to persist in DB
        try {
            const portal = getPortalRole();
            const res = await fetch('/api/v1/notifications/mark-read', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                body: JSON.stringify({ mark_all: true, portal: portal })
            });
            const data = await res.json();
            if (data.success && data.unread_count !== undefined) {
                currentUnreadCount = data.unread_count;
                updateBadgeUI(currentUnreadCount);
            }
        } catch (err) {
            console.warn('Error marking all notifications read:', err);
        } finally {
            if (markBtn) {
                markBtn.innerHTML = '<i class="bi bi-check2-all"></i> Mark all read';
                markBtn.disabled = false;
            }
        }
    };

    function showNoticeDetailsModal(item) {
        let modalEl = document.getElementById('quickNoticeViewModal');
        if (!modalEl) {
            modalEl = document.createElement('div');
            modalEl.id = 'quickNoticeViewModal';
            modalEl.className = 'modal fade';
            modalEl.setAttribute('tabindex', '-1');
            modalEl.setAttribute('aria-hidden', 'true');
            document.body.appendChild(modalEl);
        }

        const priorityBadge = item.priority === 'Urgent' ? '<span class="badge bg-danger">Urgent Alert</span>' :
                             item.priority === 'Important' ? '<span class="badge bg-warning text-dark">Important</span>' :
                             '<span class="badge bg-primary">Normal Notice</span>';

        let attachmentHtml = '';
        if (item.photo_file) {
            const fileUrl = `/uploads/notifications/${encodeURIComponent(item.photo_file)}`;
            if (item.file_type === 'image' || /\.(png|jpe?g|webp|gif)$/i.test(item.photo_file)) {
                attachmentHtml = `
                    <div class="mt-3 p-2 bg-light rounded border text-center">
                        <img src="${fileUrl}" alt="Notice Attachment" class="img-fluid rounded" style="max-height: 280px; object-fit: contain;">
                        <div class="mt-2">
                            <a href="${fileUrl}" target="_blank" download class="btn btn-sm btn-outline-primary">
                                <i class="bi bi-download me-1"></i> Download Image
                            </a>
                        </div>
                    </div>
                `;
            } else {
                attachmentHtml = `
                    <div class="mt-3 p-3 bg-light rounded border d-flex align-items-center justify-content-between">
                        <div class="d-flex align-items-center gap-2">
                            <i class="bi bi-file-earmark-pdf fs-3 text-danger"></i>
                            <div>
                                <div class="fw-semibold text-dark fs-7">${escapeHtml(item.photo_file.substring(11) || 'Attached Document')}</div>
                                <small class="text-muted">Click to view or download</small>
                            </div>
                        </div>
                        <a href="${fileUrl}" target="_blank" download class="btn btn-sm btn-primary">
                            <i class="bi bi-download me-1"></i> Open File
                        </a>
                    </div>
                `;
            }
        }

        modalEl.innerHTML = `
            <div class="modal-dialog modal-dialog-centered modal-lg">
                <div class="modal-content shadow border-0" style="border-radius: 16px; overflow: hidden;">
                    <div class="modal-header border-bottom py-3 px-4 bg-light">
                        <div class="d-flex align-items-center gap-2">
                            ${priorityBadge}
                            <span class="badge bg-secondary-subtle text-secondary">${escapeHtml(item.category)}</span>
                        </div>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body px-4 py-3">
                        <h4 class="modal-title fw-bold text-dark mb-2">${escapeHtml(item.title)}</h4>
                        <div class="d-flex flex-wrap align-items-center gap-3 text-muted fs-8 mb-3 pb-2 border-bottom">
                            <span><i class="bi bi-person-fill text-primary me-1"></i><strong>${escapeHtml(item.posted_by_name || item.posted_by_role)}</strong></span>
                            <span><i class="bi bi-calendar3 me-1"></i>${escapeHtml(item.created_at || item.time_ago)}</span>
                            ${item.end_date ? `<span><i class="bi bi-hourglass-split text-danger me-1"></i>Deadline: <strong>${escapeHtml(item.end_date)}</strong></span>` : ''}
                        </div>
                        <div class="notice-full-body fs-7 text-dark lh-base" style="white-space: pre-wrap; word-break: break-word;">
                            ${escapeHtml(item.full_message || item.message)}
                        </div>
                        ${attachmentHtml}
                    </div>
                    <div class="modal-footer border-top py-2 px-4 bg-light">
                        <button type="button" class="btn btn-secondary btn-sm" data-bs-dismiss="modal">Close</button>
                    </div>
                </div>
            </div>
        `;

        if (window.bootstrap && window.bootstrap.Modal) {
            const modalInstance = new bootstrap.Modal(modalEl);
            modalInstance.show();
        } else {
            $(modalEl).modal('show');
        }
    }

    // Initialize on DOM load
    function init() {
        fetchNotificationsFeed();

        // Setup bell button and dropdown listeners
        const bellBtn = document.getElementById('notificationBellBtn');
        const menu = document.querySelector('.notification-dropdown-menu');
        const bellIcon = document.getElementById('bellIcon');
        const badge = document.getElementById('notificationUnreadBadge');

        if (bellIcon) bellIcon.style.pointerEvents = 'none';
        if (badge) badge.style.pointerEvents = 'none';

        if (bellBtn && !bellBtn._campusBellBound) {
            bellBtn._campusBellBound = true;
            bellBtn.addEventListener('click', toggleCampusNotificationDropdown);
            bellBtn.addEventListener('touchend', function (e) {
                toggleCampusNotificationDropdown(e);
            });
        }

        if (menu && !menu._campusMenuBound) {
            menu._campusMenuBound = true;
            menu.addEventListener('click', function (e) {
                // If clicked element is not an interactive button/link/item, prevent dismissal
                const isInteractive = e.target.closest('button') || e.target.closest('a') || e.target.closest('.notification-item');
                if (!isInteractive) {
                    e.stopPropagation();
                }
            });
            menu.addEventListener('touchend', function (e) {
                const isInteractive = e.target.closest('button') || e.target.closest('a') || e.target.closest('.notification-item');
                if (!isInteractive) {
                    e.stopPropagation();
                }
            });
        }

        // Close dropdown when clicking or touching outside
        document.addEventListener('click', function (e) {
            const wrapper = document.querySelector('.notification-dropdown-wrapper');
            if (wrapper && !wrapper.contains(e.target)) {
                closeCampusNotificationDropdown();
            }
        });

        document.addEventListener('touchend', function (e) {
            const wrapper = document.querySelector('.notification-dropdown-wrapper');
            if (wrapper && !wrapper.contains(e.target)) {
                closeCampusNotificationDropdown();
            }
        });

        // Close on ESC key
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') {
                closeCampusNotificationDropdown();
            }
        });

        // Refresh feed on window focus
        window.addEventListener('focus', fetchNotificationsFeed);

        // Background polling every 30 seconds
        if (pollInterval) clearInterval(pollInterval);
        pollInterval = setInterval(fetchNotificationsFeed, 30000);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
