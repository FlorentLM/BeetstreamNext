(function () {
    'use strict';

    // Theme

    function applyTheme(theme) {
        if (theme === 'light') {
            document.documentElement.setAttribute('data-theme', 'light');
        } else {
            document.documentElement.removeAttribute('data-theme');
        }
        document.querySelectorAll('[data-action="toggle-theme"]').forEach(btn => {
            btn.setAttribute('aria-pressed', theme === 'light' ? 'true' : 'false');
        });
    }

    function toggleTheme() {
        const current = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
        const next = current === 'light' ? 'dark' : 'light';
        try {
            document.cookie = 'bsn-theme=' + next + '; Path=/; Max-Age=31536000; SameSite=Lax';
        } catch (e) {}
        applyTheme(next);
    }

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

    // Generic checkbox group select all / select none
    function toggleCheckboxGroup(containerId, checked) {
        const container = document.getElementById(containerId);
        if (!container) return;
        container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
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

    // Radio station edit modal

    function openEditRadioModal(button) {
        let station;
        try {
            station = JSON.parse(button.getAttribute('data-station'));
        } catch (err) {
            console.error('Invalid radio station payload on edit button', err);
            return;
        }

        const form = document.getElementById('editRadioForm');
        if (!form) return;

        const base = form.getAttribute('data-update-url-base') || '';
        form.action = base.slice(0, -1) + station.id;

        form.querySelector('#editRadioName').value = station.name || '';
        form.querySelector('#editRadioStreamUrl').value = station.stream_url || '';
        form.querySelector('#editRadioHomepageUrl').value = station.homepage_url || '';
        form.querySelector('#editRadioRemoveImage').checked = false;
        form.querySelector('input[type="file"]').value = '';

        const preview = document.getElementById('editRadioImagePreview');
        if (preview) {
            if (station.has_image) {
                const imgBase = preview.getAttribute('data-image-url-base') || '';
                preview.src = imgBase.slice(0, -1) + station.id;
                preview.classList.remove('hidden');
            } else {
                preview.removeAttribute('src');
                preview.classList.add('hidden');
            }
        }

        openModal('editRadioModal');
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

    function copyLogs(button) {
        const el = document.getElementById(button.dataset.target);
        if (!el) return;
        const text = el.textContent;
        const label = button.querySelector('.btn-label');

        const done = () => {
            if (!label) return;
            const orig = label.textContent;
            label.textContent = 'Copied';
            setTimeout(() => { label.textContent = orig; }, 2000);
        };

        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(done).catch(() => selectKey(el));
        } else {
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
                <td>${e.username ? escapeHtml(e.username) : '<span class="rate-limit-anon">—</span>'}</td>
                <td>${e.failures} / ${payload.max_failures}</td>
                <td>${e.oldest_failure_age_sec}s ago</td>
                <td>${e.blocked ? '<span class="badge badge-admin">BLOCKED</span>' : '<span class="badge">warning</span>'}</td>
            </tr>
        `).join('');
        container.innerHTML = `
            <table class="rate-limit-table">
                <thead><tr><th>IP</th><th>Username</th><th>Failures</th><th>Oldest</th><th>Status</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>`;
    }

    // Convert ANSI color/style escape codes to HTML
    const ANSI_COLOR_CLASS = {
        30: 'ansi-fg-black', 31: 'ansi-fg-red', 32: 'ansi-fg-green', 33: 'ansi-fg-yellow',
        34: 'ansi-fg-blue', 35: 'ansi-fg-magenta', 36: 'ansi-fg-cyan', 37: 'ansi-fg-white',
        90: 'ansi-fg-bright-black', 91: 'ansi-fg-bright-red', 92: 'ansi-fg-bright-green',
        93: 'ansi-fg-bright-yellow', 94: 'ansi-fg-bright-blue', 95: 'ansi-fg-bright-magenta',
        96: 'ansi-fg-bright-cyan', 97: 'ansi-fg-bright-white'
    };

    function ansiLineToHtml(line) {
        let html = '';
        let openSpan = false;
        let colorClass = null, bold = false, dim = false, italic = false, underline = false;

        function closeSpan() {
            if (openSpan) { html += '</span>'; openSpan = false; }
        }

        function openSpanIfStyled() {
            const classes = [];
            if (colorClass) classes.push(colorClass);
            if (bold) classes.push('ansi-bold');
            if (dim) classes.push('ansi-dim');
            if (italic) classes.push('ansi-italic');
            if (underline) classes.push('ansi-underline');
            if (classes.length) {
                html += '<span class="' + classes.join(' ') + '">';
                openSpan = true;
            }
        }

        const parts = line.split(/(\x1b\[[0-9;]*[a-zA-Z])/);
        for (const part of parts) {
            const m = /^\x1b\[([0-9;]*)([a-zA-Z])$/.exec(part);
            if (m) {
                if (m[2] !== 'm') continue;   // not a color/style code, skip
                const codes = m[1] ? m[1].split(';').map(Number) : [0];
                closeSpan();
                for (const code of codes) {
                    if (code === 0) { colorClass = null; bold = dim = italic = underline = false; }
                    else if (code === 1) bold = true;
                    else if (code === 2) dim = true;
                    else if (code === 3) italic = true;
                    else if (code === 4) underline = true;
                    else if (code === 22) { bold = false; dim = false; }
                    else if (code === 23) italic = false;
                    else if (code === 24) underline = false;
                    else if (code === 39) colorClass = null;
                    else if (ANSI_COLOR_CLASS[code]) colorClass = ANSI_COLOR_CLASS[code];
                }
                openSpanIfStyled();
            } else if (part) {
                html += escapeHtml(part);
            }
        }
        closeSpan();
        return html;
    }

    async function refreshLogs(button) {
        const url = button.dataset.url;
        const target = document.getElementById(button.dataset.target);
        const hint = document.getElementById(button.dataset.hint);
        if (!target || !url) return;
        try {
            const resp = await fetch(url, { credentials: 'same-origin' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const payload = await resp.json();
            const lines = payload.lines || [];
            const wasAtBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 20;
            target.innerHTML = lines.length ? lines.map(ansiLineToHtml).join('\n') : '(no log output yet)';
            if (wasAtBottom) target.scrollTop = target.scrollHeight;
            if (hint) hint.textContent = `Last ${lines.length} log line${lines.length !== 1 ? 's' : ''} captured since server start.`;
        } catch (err) {
            target.textContent = 'Failed to load log: ' + err.message;
        }
    }

    let logAutoRefreshTimer = null;

    function toggleLogAutoRefresh(checkbox) {
        if (logAutoRefreshTimer) {
            clearInterval(logAutoRefreshTimer);
            logAutoRefreshTimer = null;
        }
        if (checkbox.checked) {
            const button = document.getElementById(checkbox.dataset.refreshTarget);
            if (!button) return;
            logAutoRefreshTimer = setInterval(() => refreshLogs(button), 5000);
        }
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

    async function refreshScanStatus(button) {
        const url = button.dataset.url;
        const statusEl = document.getElementById('beets-scan-status-value');
        const countEl = document.getElementById('beets-scan-count-value');
        if (!url || !statusEl) return;
        try {
            const resp = await fetch(url, { credentials: 'same-origin' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const payload = await resp.json();
            statusEl.textContent = payload.scanning ? 'Scanning…' : 'Idle';
            if (countEl) countEl.textContent = payload.count != null ? payload.count : '—';
        } catch (err) {
            statusEl.textContent = 'Failed to load: ' + err.message;
        }
    }

    async function testConnection(button) {
        const url = button.dataset.url;
        const result = document.getElementById(button.dataset.result);
        if (!url || !result) return;

        button.disabled = true;
        result.className = 'test-result';
        result.textContent = 'Testing...';
        try {
            const resp = await fetch(url, { credentials: 'same-origin' });
            const payload = await resp.json();
            result.className = 'test-result ' + (payload.ok ? 'test-result-ok' : 'test-result-fail');
            result.textContent = payload.message || (payload.ok ? 'OK' : 'Failed');
        } catch (err) {
            result.className = 'test-result test-result-fail';
            result.textContent = 'Failed to test connection: ' + err.message;
        } finally {
            button.disabled = false;
        }
    }

    async function discoverSonosSpeakers(button) {
        const url = button.dataset.url;
        const select = document.getElementById(button.dataset.select);
        const result = document.getElementById(button.dataset.result);
        if (!url || !select) return;

        const previousValue = select.value;

        button.disabled = true;
        if (result) { result.className = 'test-result'; result.textContent = 'Searching...'; }

        try {
            const resp = await fetch(url, { credentials: 'same-origin' });
            const payload = await resp.json();
            const speakers = payload.speakers || [];

            select.innerHTML = '';

            speakers.forEach(sp => {
                const opt = document.createElement('option');
                opt.value = sp.ip;
                opt.textContent = `${sp.name} (${sp.ip})`;
                if (sp.ip === previousValue) opt.selected = true;
                select.appendChild(opt);
            });

            if (previousValue && !speakers.some(sp => sp.ip === previousValue)) {
                const opt = document.createElement('option');
                opt.value = previousValue;
                opt.textContent = `${previousValue} (current, not found)`;
                opt.selected = true;
                select.insertBefore(opt, select.firstChild);
            } else if (speakers.length === 0) {
                const opt = document.createElement('option');
                opt.value = '';
                opt.textContent = 'No speakers found';
                select.appendChild(opt);
            }

            if (result) {
                result.className = 'test-result ' + (payload.ok ? 'test-result-ok' : 'test-result-fail');
                result.textContent = payload.message || (payload.ok ? 'OK' : 'Failed');
            }
        } catch (err) {
            if (result) {
                result.className = 'test-result test-result-fail';
                result.textContent = 'Discovery failed: ' + err.message;
            }
        } finally {
            button.disabled = false;
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
            case 'edit-radio':
                openEditRadioModal(target);
                break;
            case 'roles-toggle':
                toggleRoles(target.dataset.target, target.dataset.value === 'true', target.dataset.skip);
                break;
            case 'checkbox-group-toggle':
                toggleCheckboxGroup(target.dataset.target, target.dataset.value === 'true');
                break;
            case 'copy-api-key':
                copyApiKey(target);
                break;
            case 'copy-log':
                copyLogs(target);
                break;
            case 'refresh-rate-limits':
                refreshRateLimits(target);
                break;
            case 'refresh-scan-status':
                refreshScanStatus(target);
                break;
            case 'refresh-log':
                refreshLogs(target);
                break;
            case 'test-connection':
                testConnection(target);
                break;
            case 'discover-sonos-speakers':
                discoverSonosSpeakers(target);
                break;
            case 'toggle-theme':
                toggleTheme();
                break;
            case 'edit-chat':
                const msgId = target.dataset.id;
                const oldText = target.dataset.text;
                const newText = window.prompt("Edit user's chat message:", oldText);
                if (newText !== null && newText.trim() !== "") {
                    const form = document.createElement('form');
                    form.method = 'POST';
                    form.action = `/admin/chat/edit/${msgId}`;

                    const csrfInput = document.createElement('input');
                    csrfInput.type = 'hidden';
                    csrfInput.name = 'csrf_token';
                    csrfInput.value = document.querySelector('input[name="csrf_token"]').value;
                    form.appendChild(csrfInput);

                    const msgInput = document.createElement('input');
                    msgInput.type = 'hidden';
                    msgInput.name = 'message';
                    msgInput.value = newText;
                    form.appendChild(msgInput);

                    document.body.appendChild(form);
                    form.submit();
                }
                break;
        }
    });

    document.addEventListener('change', event => {
        const target = event.target.closest('[data-action="toggle-log-autorefresh"]');
        if (target) toggleLogAutoRefresh(target);
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

    applyTheme(document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark');
    initTabsFromHash();

    const rateLimitsRefreshBtn = document.querySelector('[data-action="refresh-rate-limits"]');
    if (rateLimitsRefreshBtn) refreshRateLimits(rateLimitsRefreshBtn);

    const scanStatusRefreshBtn = document.querySelector('[data-action="refresh-scan-status"]');
    if (scanStatusRefreshBtn) refreshScanStatus(scanStatusRefreshBtn);

    document.querySelectorAll('[data-action="refresh-log"]').forEach(refreshLogs);

    // Format HLS/chat epoch timestamps to human readable format
    document.querySelectorAll('.chat-time').forEach(el => {
        const ms = parseInt(el.dataset.timestamp);
        if (!isNaN(ms)) {
            el.textContent = new Date(ms).toLocaleString();
        }
    });

    // Auto-show the one-time API key modal if the server rendered one
    // Not dismissed by backdrop click just to be sure
    document.querySelectorAll('.modal-overlay[data-autoshow]').forEach(m => {
        m.classList.add('active');
    });
})();
