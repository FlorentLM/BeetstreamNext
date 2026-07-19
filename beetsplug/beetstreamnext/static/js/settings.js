(function () {
    'use strict';

    // Tabs

    function activateTab(name) {
        const tabs = Array.from(document.querySelectorAll('.tab'));
        if (tabs.length === 0) return;
        const valid = tabs.map(t => t.dataset.tab);
        if (!valid.includes(name)) name = valid[0];

        tabs.forEach(t => {
            const active = t.dataset.tab === name;
            t.classList.toggle('active', active);
            t.setAttribute('aria-selected', active ? 'true' : 'false');
        });
        document.querySelectorAll('.tab-content').forEach(c => {
            c.classList.toggle('active', c.dataset.tabContent === name);
        });
    }

    function initTabsFromHash() {
        const initial = (window.location.hash || '').replace('#', '') || 'users';
        activateTab(initial);
    }

    window.addEventListener('hashchange', initTabsFromHash);

    // Modals

    function openModal(id) {
        const el = document.getElementById(id);
        if (el) el.classList.add('active');
    }

    function closeModal(id) {
        const el = document.getElementById(id);
        if (el) el.classList.remove('active');
    }

    function closeAllModals() {
        document.querySelectorAll('.modal-overlay.active').forEach(m => {
            m.classList.remove('active');
        });
    }

    // Role checkboxes
    // `data-skip` (comma-separated names) excludes specific roles
    // (used to keep "select all" from giving admin)

    function toggleRoles(formId, checked, skip) {
        const form = document.getElementById(formId);
        if (!form) return;
        const skipSet = new Set((skip || '').split(',').map(s => s.trim()).filter(Boolean));
        form.querySelectorAll('.roles-grid input[type="checkbox"]').forEach(cb => {
            if (checked && skipSet.has(cb.name)) return;
            cb.checked = checked;
        });
    }

    // Edit modal

    function applyTemplateUrl(el, attr, username) {
        const tmpl = el.getAttribute('data-update-url') || el.getAttribute('data-avatar-url') || '';
        el[attr] = tmpl.replace('__USERNAME__', encodeURIComponent(username));
    }

    function openEditModal(button) {
        let userData;
        try {
            userData = JSON.parse(button.getAttribute('data-user'));
        } catch (err) {
            console.error('Invalid user payload on edit button', err);
            return;
        }

        const form = document.getElementById('editForm');
        if (!form) return;

        applyTemplateUrl(form, 'action', userData.username);

        const avatarUpload = document.getElementById('avatarUploadForm');
        const avatarDelete = document.getElementById('avatarDeleteForm');
        if (avatarUpload) applyTemplateUrl(avatarUpload, 'action', userData.username);
        if (avatarDelete) {
            applyTemplateUrl(avatarDelete, 'action', userData.username);
            avatarDelete.classList.toggle('hidden', !userData.hasAvatar);
        }

        // Avatar preview (src only set when one exists)
        const preview = document.getElementById('editAvatarPreview');
        if (preview) {
            if (userData.hasAvatar) {
                const tmpl = preview.getAttribute('data-avatar-url') || '';
                preview.src = tmpl.replace('__USERNAME__', encodeURIComponent(userData.username))
                    + '?v=' + Math.trunc(userData.avatarLastChanged || 0);
                preview.classList.remove('hidden');
            } else {
                preview.removeAttribute('src');
                preview.classList.add('hidden');
            }
        }

        const nameEl = document.getElementById('editModalUsername');
        if (nameEl) nameEl.textContent = userData.username;

        const pwField = form.querySelector('[name="password"]');
        if (pwField) pwField.value = '';

        const emailField = form.querySelector('[name="email"]');
        if (emailField) emailField.value = userData.email || '';

        // Sync every checkbox in the form
        form.querySelectorAll('input[type="checkbox"]').forEach(cb => {
            if (cb.name in userData) cb.checked = !!userData[cb.name];
        });

        const bitrate = form.querySelector('[name="maxBitRate"]');
        if (bitrate) bitrate.value = userData.maxBitRate || 0;

        openModal('editModal');
    }

    // One-time API key copy

    function copyApiKey(button) {
        const el = document.getElementById('apiKeyValue');
        if (!el) return;
        const key = el.textContent.trim();

        const done = () => {
            button.textContent = 'Copied';
            setTimeout(() => { button.textContent = 'Copy'; }, 2000);
        };

        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(key).then(done).catch(() => selectKey(el));
        } else {
            // No clipboard API over plain HTTP
            selectKey(el);
        }
    }

    function selectKey(el) {
        const range = document.createRange();
        range.selectNodeContents(el);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
    }

    // Rate-limit panel

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
        }[c]));
    }

    function renderRateLimitState(container, payload) {
        if (!payload.entries || payload.entries.length === 0) {
            container.innerHTML = `<p class="empty-state">No active rate-limit failures.
                Blocking at ${payload.max_failures} failures within ${payload.block_window_sec}s.</p>`;
            return;
        }
        const rows = payload.entries.map(e => `
            <tr class="${e.blocked ? 'rate-limit-blocked' : ''}">
                <td><code>${escapeHtml(e.ip)}</code></td>
                <td>${e.failures} / ${payload.max_failures}</td>
                <td>${e.oldest_failure_age_sec}s ago</td>
                <td>${e.blocked ? '<span class="badge badge-admin">BLOCKED</span>' : '<span class="badge">warning</span>'}</td>
            </tr>
        `).join('');
        container.innerHTML = `
            <table class="rate-limit-table">
                <thead><tr><th>IP</th><th>Failures</th><th>Oldest</th><th>Status</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>`;
    }

    async function refreshRateLimits(button) {
        const url = button.dataset.url;
        const container = document.getElementById('rate-limit-state');
        if (!container || !url) return;
        container.innerHTML = '<p class="empty-state">Loading...</p>';
        try {
            const resp = await fetch(url, { credentials: 'same-origin' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const payload = await resp.json();
            renderRateLimitState(container, payload);
        } catch (err) {
            container.innerHTML = `<p class="empty-state">Failed to load: ${escapeHtml(err.message)}</p>`;
        }
    }

    // Events

    document.addEventListener('click', event => {
        const tab = event.target.closest('.tab[data-tab]');
        if (tab) {
            activateTab(tab.dataset.tab);
            // replaceState so switching tabs doesn't pollute history
            history.replaceState(null, '', '#' + tab.dataset.tab);
            return;
        }

        const target = event.target.closest('[data-action]');
        if (!target) return;

        switch (target.dataset.action) {
            case 'open-modal':
                openModal(target.dataset.target);
                break;
            case 'close-modal':
                closeModal(target.dataset.target);
                break;
            case 'modal-backdrop':
                if (event.target === target) closeModal(target.id);
                break;
            case 'edit-user':
                openEditModal(target);
                break;
            case 'roles-toggle':
                toggleRoles(target.dataset.target, target.dataset.value === 'true', target.dataset.skip);
                break;
            case 'copy-api-key':
                copyApiKey(target);
                break;
            case 'refresh-rate-limits':
                refreshRateLimits(target);
                break;
        }
    });

    // Confirm dialogs
    document.addEventListener('submit', event => {
        const form = event.target.closest('form[data-confirm]');
        if (!form) return;
        if (!window.confirm(form.dataset.confirm)) {
            event.preventDefault();
        }
    });

    // Esc to close open modals
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') closeAllModals();
    });

    // Init

    initTabsFromHash();

    // Auto-show the one-time API key modal if the server rendered one
    // Not dismissed by backdrop click just to be sure
    document.querySelectorAll('.modal-overlay[data-autoshow]').forEach(m => {
        m.classList.add('active');
    });
})();
