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
        if (id === 'confirmModal') settleConfirm(false);
        if (id === 'promptModal') settlePrompt(null);
    }

    function closeAllModals() {
        document.querySelectorAll('.modal-overlay.active').forEach(m => {
            m.classList.remove('active');
        });
        settleConfirm(false);
        settlePrompt(null);
    }

    let confirmSettle = null;
    let promptSettle = null;

    function settleConfirm(result) {
        if (!confirmSettle) return;
        const settle = confirmSettle;
        confirmSettle = null;
        settle(result);
    }

    function settlePrompt(result) {
        if (!promptSettle) return;
        const settle = promptSettle;
        promptSettle = null;
        settle(result);
    }

    function confirmModal(message) {
        return new Promise(resolve => {
            confirmSettle = resolve;
            document.getElementById('confirmMessage').textContent = message;
            openModal('confirmModal');
        });
    }

    function submitPrompt() {
        const input = document.getElementById('promptInput');
        settlePrompt(input ? input.value : '');
        closeModal('promptModal');
    }

    function promptModal(message, defaultValue) {
        return new Promise(resolve => {
            promptSettle = resolve;
            document.getElementById('promptMessage').textContent = message;
            const input = document.getElementById('promptInput');
            if (input) {
                input.value = defaultValue || '';
                openModal('promptModal');
                setTimeout(() => { input.focus(); input.select(); }, 50);
            } else {
                openModal('promptModal');
            }
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
        form.querySelector('#editRadioImage').value = '';

        const preview = document.getElementById('editRadioImagePreview');
        if (preview) {
            if (station.has_image) {
                const imgTmpl = preview.getAttribute('data-image-url-tmpl') || '';
                preview.src = imgTmpl.replace('__STATION_ID__', station.id);
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
            navigator.clipboard.writeText(key).then(done).catch(() => { if (copySel(key)) done(); });
        } else {
            // No Clipboard API over plain HTTP, fallback to execCommand
            if (copySel(key)) done();
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
            navigator.clipboard.writeText(text).then(done).catch(() => { if (copySel(text)) done(); });
        } else {
            if (copySel(text)) done();
        }
    }

    function copySel(text) {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.left = '-9999px';
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        let ok = false;
        try {
            ok = document.execCommand('copy');
        } catch (e) {
            // ignore
        }
        document.body.removeChild(ta);
        return ok;
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
        if (!target || !url) return;
        try {
            const resp = await fetch(url, { credentials: 'same-origin' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const payload = await resp.json();
            const lines = payload.lines || [];
            const wasAtBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 20;
            target.innerHTML = lines.length ? lines.map(ansiLineToHtml).join('\n') : '(no log output yet)';
            if (wasAtBottom) target.scrollTop = target.scrollHeight;
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

    function toggleRowDetail(row) {
        const detailRow = row.nextElementSibling;
        if (!detailRow || !detailRow.classList.contains('row-detail')) return;
        detailRow.hidden = !detailRow.hidden;
        row.classList.toggle('expanded', !detailRow.hidden);
    }

    async function refreshHealthStatus(button) {
        const url = button.dataset.url;
        const statusEl = document.getElementById('health-scan-status-value');
        const totalEl = document.getElementById('health-scan-total-value');
        const countEl = document.getElementById('health-scan-count-value');
        if (!url || !statusEl) return;
        try {
            const resp = await fetch(url, { credentials: 'same-origin' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const payload = await resp.json();
            statusEl.textContent = payload.scanning ? 'Scanning…' : 'Idle';
            if (totalEl) totalEl.textContent = payload.total != null ? payload.total : '—';
            if (countEl) countEl.textContent = payload.flagged != null ? payload.flagged : '—';
        } catch (err) {
            statusEl.textContent = 'Failed to load: ' + err.message;
        }
    }

    // Podcast episode dl status polling
    function formatBytes(bytes) {
        if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        if (bytes >= 1024) return Math.round(bytes / 1024) + ' KB';
        return bytes + ' B';
    }

    let podcastPollTimer = null;

    async function refreshPodcastStatuses() {
        try {
            const resp = await fetch('/admin/podcasts/status', { credentials: 'same-origin' });
            if (!resp.ok) return;
            const data = await resp.json();
            let busy = false;

            for (const [id, info] of Object.entries(data.channels || {})) {
                const badge = document.getElementById(`podcast-channel-status-${id}`);
                if (badge && badge.dataset.status !== info.status) {
                    badge.textContent = info.status;
                    badge.dataset.status = info.status;
                    badge.classList.toggle('badge-admin', info.status === 'error');
                }

                const sizeEl = document.getElementById(`podcast-channel-size-${id}`);
                if (sizeEl && info.storage_size !== undefined && sizeEl.textContent !== info.storage_size) {
                    sizeEl.textContent = info.storage_size;
                }

                if (info.status === 'new' || info.status === 'downloading') busy = true;
            }

            for (const [id, info] of Object.entries(data.episodes || {})) {
                const badge = document.getElementById(`podcast-episode-status-${id}`);
                if (badge && badge.dataset.status !== info.status) {
                    badge.textContent = info.status;
                    badge.dataset.status = info.status;
                    badge.classList.toggle('badge-admin', info.status === 'error');

                    const dl = document.getElementById(`podcast-episode-dl-${id}`);
                    const cancel = document.getElementById(`podcast-episode-cancel-${id}`);
                    const del = document.getElementById(`podcast-episode-del-${id}`);
                    if (dl) dl.hidden = info.status === 'completed' || info.status === 'downloading';
                    if (cancel) cancel.hidden = info.status !== 'downloading';
                    if (del) del.hidden = info.status !== 'completed';

                    const sizeEl = document.getElementById(`podcast-episode-size-${id}`);
                    if (sizeEl && info.file_size) {
                        sizeEl.innerHTML = `<span class="badge badge-limit">${formatBytes(info.file_size)}</span>`;
                    }
                }
                if (info.status === 'downloading') busy = true;
            }

            if (!busy && podcastPollTimer) {
                clearInterval(podcastPollTimer);
                podcastPollTimer = null;
            }
        } catch (err) {
            // network hiccup, next iter retries
        }
    }

    function startPodcastPollingIfBusy() {
        if (podcastPollTimer) return;
        const busySelector = '[id^="podcast-channel-status-"][data-status="new"], '
            + '[id^="podcast-channel-status-"][data-status="downloading"], '
            + '[id^="podcast-episode-status-"][data-status="downloading"]';
        if (document.querySelector(busySelector)) {
            podcastPollTimer = setInterval(refreshPodcastStatuses, 3000);
        }
    }

    // Lazy fetch of episodes lists

    function formatDuration(seconds) {
        const total = Math.max(0, Math.floor(Number(seconds) || 0));
        const hours = Math.floor(total / 3600);
        const minutes = Math.floor((total % 3600) / 60);
        const secs = total % 60;
        const pad = n => String(n).padStart(2, '0');
        return hours ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`;
    }

    const PODCAST_EPISODE_ICONS = {
        download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M5 21h14"/></svg>',
        cancel: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>',
        delete: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>',
    };

    function podcastEpisodeRowHtml(e, csrfToken) {
        const title = escapeHtml(e.title || '(untitled)');
        const dateHtml = e.publish_date
            ? `<span class="chat-time">${escapeHtml(new Date(e.publish_date * 1000).toLocaleString())}</span>`
            : '<span class="rate-limit-anon">&mdash;</span>';
        const sizeHtml = e.file_size
            ? `<span class="badge badge-limit">${formatBytes(e.file_size)}</span>`
            : '<span class="rate-limit-anon">&mdash;</span>';
        const csrfInput = `<input type="hidden" name="csrf_token" value="${escapeHtml(csrfToken)}">`;
        const dlHidden = (e.status === 'completed' || e.status === 'downloading') ? 'hidden' : '';
        const cancelHidden = e.status !== 'downloading' ? 'hidden' : '';
        const delHidden = e.status !== 'completed' ? 'hidden' : '';

        return `
            <tr>
                <td>${title}</td>
                <td>${dateHtml}</td>
                <td>${escapeHtml(formatDuration(e.duration))}</td>
                <td><span class="badge${e.status === 'error' ? ' badge-admin' : ''}"
                          id="podcast-episode-status-${e.id}" data-status="${e.status}">${escapeHtml(e.status)}</span></td>
                <td><span id="podcast-episode-size-${e.id}">${sizeHtml}</span></td>
                <td class="actions-cell">
                    <form id="podcast-episode-dl-${e.id}" ${dlHidden}
                          action="/admin/podcasts/episode/${e.id}/download" method="POST" class="form-inline">
                        ${csrfInput}
                        <button type="submit" class="icon-btn" title="Download" aria-label="Download &quot;${title}&quot;">${PODCAST_EPISODE_ICONS.download}</button>
                    </form>
                    <form id="podcast-episode-cancel-${e.id}" ${cancelHidden}
                          action="/admin/podcasts/episode/${e.id}/cancel-download" method="POST" class="form-inline">
                        ${csrfInput}
                        <button type="submit" class="icon-btn icon-btn-delete" title="Cancel download" aria-label="Cancel download of &quot;${title}&quot;">${PODCAST_EPISODE_ICONS.cancel}</button>
                    </form>
                    <form id="podcast-episode-del-${e.id}" ${delHidden}
                          action="/admin/podcasts/episode/${e.id}/delete" method="POST" class="form-inline"
                          data-confirm="Remove this episode's downloaded file for all subscribers?">
                        ${csrfInput}
                        <button type="submit" class="icon-btn icon-btn-delete" title="Delete file" aria-label="Delete downloaded file for &quot;${title}&quot;">${PODCAST_EPISODE_ICONS.delete}</button>
                    </form>
                </td>
            </tr>`;
    }

    async function loadPodcastEpisodes(details) {
        if (details.dataset.loaded) return;
        const channelId = details.dataset.channelId;
        const body = details.querySelector('[data-episodes-body]');
        if (!channelId || !body) return;

        const csrfField = document.querySelector('#importOpmlForm input[name="csrf_token"]');
        const csrfToken = csrfField ? csrfField.value : '';

        try {
            const resp = await fetch(`/admin/podcasts/${channelId}/episodes`, { credentials: 'same-origin' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const episodes = await resp.json();
            details.dataset.loaded = 'true';

            if (!episodes.length) {
                body.innerHTML = '<p class="empty-state">No episodes yet.</p>';
                return;
            }

            body.innerHTML = `
                <table>
                    <thead>
                        <tr>
                            <th>Title</th>
                            <th>Published</th>
                            <th>Duration</th>
                            <th>Status</th>
                            <th>Size</th>
                            <th class="cell-right">Actions</th>
                        </tr>
                    </thead>
                    <tbody>${episodes.map(e => podcastEpisodeRowHtml(e, csrfToken)).join('')}</tbody>
                </table>`;

            startPodcastPollingIfBusy();
        } catch (err) {
            body.innerHTML = `<p class="empty-state">Failed to load episodes: ${escapeHtml(err.message)}</p>`;
        }
    }

    // Beets config editor

    function formatConfigTime(el) {
        const ms = parseInt(el.dataset.timestamp);
        el.textContent = isNaN(ms) ? '—' : new Date(ms).toLocaleTimeString();
    }

    function applyBeetsConfigState(payload) {
        const editor = document.getElementById('beetsConfigEditor');
        const pathEl = document.getElementById('beetsConfigPath');
        const badge = document.getElementById('beetsConfigReadOnlyBadge');
        const saveBtn = document.getElementById('beetsConfigSaveBtn');
        const loadedEl = document.getElementById('beetsConfigLoadedAt');
        if (!editor) return;

        if (payload.content !== undefined) editor.value = payload.content;
        if (payload.path !== undefined && pathEl) pathEl.textContent = payload.path;
        if (payload.read_only !== undefined) {
            editor.readOnly = payload.read_only;
            if (badge) badge.hidden = !payload.read_only;
            if (saveBtn) saveBtn.disabled = payload.read_only;
        }
        if (payload.loaded_at !== undefined && loadedEl) {
            loadedEl.dataset.timestamp = String(payload.loaded_at * 1000);
            formatConfigTime(loadedEl);
        }
    }

    async function reloadBeetsConfig(button) {
        const url = button.dataset.url;
        const result = document.getElementById('beetsConfigResult');
        if (!url) return;

        button.disabled = true;
        try {
            const resp = await fetch(url, { credentials: 'same-origin' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            applyBeetsConfigState(await resp.json());
            if (result) { result.className = 'test-result config-editor-result'; result.textContent = ''; }
        } catch (err) {
            if (result) {
                result.className = 'test-result config-editor-result test-result-fail';
                result.textContent = 'Failed to reload: ' + err.message;
            }
        } finally {
            button.disabled = false;
        }
    }

    async function saveBeetsConfig(button) {
        const url = button.dataset.url;
        const editor = document.getElementById('beetsConfigEditor');
        const result = document.getElementById('beetsConfigResult');
        if (!url || !editor) return;

        const csrfInput = document.querySelector('input[name="csrf_token"]');
        button.disabled = true;
        try {
            const resp = await fetch(url, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfInput ? csrfInput.value : ''
                },
                body: JSON.stringify({ content: editor.value })
            });
            const payload = await resp.json();
            applyBeetsConfigState(payload);
            if (result) {
                result.className = 'test-result config-editor-result ' + (payload.ok ? 'test-result-ok' : 'test-result-fail');
                result.textContent = payload.message || (payload.ok ? 'Saved.' : 'Failed to save.');
            }
        } catch (err) {
            button.disabled = false;
            if (result) {
                result.className = 'test-result config-editor-result test-result-fail';
                result.textContent = 'Failed to save: ' + err.message;
            }
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

    async function discoverDevices(button) {
        const url = button.dataset.url;
        const input = document.getElementById(button.dataset.select);
        const datalist = document.getElementById(button.dataset.datalist);
        const result = document.getElementById(button.dataset.result);
        if (!url || !input || !datalist) return;

        const previousValue = input.value;

        const backendSelect = document.getElementById('set-jukebox_backend');
        const discoverUrl = backendSelect ? `${url}?backend=${encodeURIComponent(backendSelect.value)}` : url;

        button.disabled = true;
        if (result) { result.className = 'test-result'; result.textContent = 'Searching...'; }

        try {
            const resp = await fetch(discoverUrl, { credentials: 'same-origin' });
            const payload = await resp.json();
            const devices = payload.devices || [];

            datalist.innerHTML = '';

            devices.forEach(dev => {
                const opt = document.createElement('option');
                opt.value = dev.id;
                opt.label = `${dev.name} (${dev.detail})`;
                datalist.appendChild(opt);
            });

            // only fill suggest when the field is empty and discovery found something
            if (!previousValue && devices.length === 1) {
                input.value = devices[0].id;
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

    async function searchRadioStations(button) {
        const url = button.dataset.url;
        const input = document.getElementById(button.dataset.input);
        const results = document.getElementById(button.dataset.results);
        if (!url || !input || !results) return;

        const q = input.value.trim();
        if (!q) return;

        button.disabled = true;
        results.classList.remove('hidden');
        results.innerHTML = '';
        const status = document.createElement('p');
        status.className = 'test-result radio-search-status';
        status.textContent = 'Searching...';
        results.appendChild(status);

        try {
            const resp = await fetch(`${url}?q=${encodeURIComponent(q)}`, { credentials: 'same-origin' });
            const payload = await resp.json();
            const stations = payload.stations || [];

            results.innerHTML = '';

            if (!stations.length) {
                const p = document.createElement('p');
                p.className = 'test-result test-result-fail';
                p.textContent = payload.message || 'No stations found.';
                results.appendChild(p);
                return;
            }

            stations.forEach(station => {
                const item = document.createElement('button');
                item.type = 'button';
                item.className = 'radio-result-item';
                item.dataset.action = 'use-radio-result';
                item.dataset.name = station.name || '';
                item.dataset.streamUrl = station.stream_url || '';
                item.dataset.homepageUrl = station.homepage_url || '';
                item.dataset.favicon = station.favicon || '';

                const name = document.createElement('span');
                name.className = 'radio-result-name';
                name.textContent = station.name || '(unnamed)';
                item.appendChild(name);

                const streamUrl = document.createElement('span');
                streamUrl.className = 'radio-result-url';
                streamUrl.textContent = station.stream_url || '';
                item.appendChild(streamUrl);

                results.appendChild(item);
            });
        } catch (err) {
            results.innerHTML = '';
            const p = document.createElement('p');
            p.className = 'test-result test-result-fail';
            p.textContent = 'Search failed: ' + err.message;
            results.appendChild(p);
        } finally {
            button.disabled = false;
        }
    }

    async function searchPodcasts(button) {
        const url = button.dataset.url;
        const input = document.getElementById(button.dataset.input);
        const results = document.getElementById(button.dataset.results);
        if (!url || !input || !results) return;

        const q = input.value.trim();
        if (!q) return;

        button.disabled = true;
        results.classList.remove('hidden');
        results.innerHTML = '';
        const status = document.createElement('p');
        status.className = 'test-result radio-search-status';
        status.textContent = 'Searching...';
        results.appendChild(status);

        try {
            const resp = await fetch(`${url}?q=${encodeURIComponent(q)}`, { credentials: 'same-origin' });
            const payload = await resp.json();
            const feeds = payload.feeds || [];

            results.innerHTML = '';

            if (!feeds.length) {
                const p = document.createElement('p');
                p.className = 'test-result test-result-fail';
                p.textContent = payload.message || 'No podcasts found.';
                results.appendChild(p);
                return;
            }

            feeds.forEach(feed => {
                const item = document.createElement('button');
                item.type = 'button';
                item.className = 'radio-result-item';
                item.dataset.action = 'use-podcast-result';
                item.dataset.url = feed.url || '';

                const title = document.createElement('span');
                title.className = 'radio-result-name';
                title.textContent = feed.title || '(untitled)';
                item.appendChild(title);

                const feedUrl = document.createElement('span');
                feedUrl.className = 'radio-result-url';
                feedUrl.textContent = feed.url || '';
                item.appendChild(feedUrl);

                results.appendChild(item);
            });
        } catch (err) {
            results.innerHTML = '';
            const p = document.createElement('p');
            p.className = 'test-result test-result-fail';
            p.textContent = 'Search failed: ' + err.message;
            results.appendChild(p);
        } finally {
            button.disabled = false;
        }
    }

    function usePodcastResult(target) {
        const urlInput = document.getElementById('podcastFeedUrl');
        if (urlInput) urlInput.value = target.dataset.url || '';

        const results = document.getElementById('podcastDiscoveryResults');
        if (results) results.classList.add('hidden');
    }

    async function useRadioResult(target) {
        const nameInput = document.getElementById('createRadioName');
        const streamInput = document.getElementById('createRadioStreamUrl');
        const homepageInput = document.getElementById('createRadioHomepageUrl');
        if (nameInput) nameInput.value = target.dataset.name || '';
        if (streamInput) streamInput.value = target.dataset.streamUrl || '';
        if (homepageInput) homepageInput.value = target.dataset.homepageUrl || '';

        const favicon = target.dataset.favicon || '';
        const faviconInput = document.getElementById('createRadioFavicon');

        // Kept for create_station() in case the icon fetch here fails client-side
        if (faviconInput) faviconInput.value = favicon;

        const imageInput = document.getElementById('createRadioImage');
        if (imageInput) imageInput.value = '';

        const results = document.getElementById('radioDiscoveryResults');
        if (results) results.classList.add('hidden');

        const preview = document.getElementById('createRadioIconPreview');
        if (!preview) return;

        if (preview.dataset.blobUrl) {
            URL.revokeObjectURL(preview.dataset.blobUrl);
            delete preview.dataset.blobUrl;
        }
        preview.removeAttribute('src');
        preview.classList.add('hidden');

        const searchButton = document.querySelector('[data-action="discover-radios"]');
        const proxyBase = searchButton ? searchButton.dataset.faviconProxy : '';
        const name = target.dataset.name || '';
        const homepage = target.dataset.homepageUrl || '';
        if (!proxyBase || !name) return;

        // Fetch the resolved icon here so it can be reused in create_station()
        const params = new URLSearchParams({ name });
        if (favicon) params.set('url', favicon);
        if (homepage) params.set('homepage', homepage);

        try {
            const resp = await fetch(`${proxyBase}?${params.toString()}`, { credentials: 'same-origin' });
            if (!resp.ok) return;
            const blob = await resp.blob();

            const blobUrl = URL.createObjectURL(blob);
            preview.dataset.blobUrl = blobUrl;
            preview.src = blobUrl;
            preview.classList.remove('hidden');

            if (imageInput && typeof DataTransfer !== 'undefined') {
                const file = new File([blob], 'icon', { type: blob.type || 'application/octet-stream' });
                const dt = new DataTransfer();
                dt.items.add(file);
                imageInput.files = dt.files;
            }
        } catch (err) {
            // Left empty, favicon_url still lets create_station() try server-side
        }
    }

    function previewLocalRadioIcon(fileInput, previewId, faviconInputId) {
        const file = fileInput.files[0];
        const preview = document.getElementById(previewId);
        if (faviconInputId) {
            const faviconInput = document.getElementById(faviconInputId);
            if (faviconInput) faviconInput.value = '';
        }

        if (preview && preview.dataset.blobUrl) {
            URL.revokeObjectURL(preview.dataset.blobUrl);
            delete preview.dataset.blobUrl;
        }

        if (file && preview) {
            const reader = new FileReader();
            reader.onload = () => {
                preview.src = reader.result;
                preview.classList.remove('hidden');
            };
            reader.readAsDataURL(file);
        } else if (preview) {
            preview.removeAttribute('src');
            preview.classList.add('hidden');
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
            case 'refresh-health-status':
                refreshHealthStatus(target);
                break;
            case 'toggle-row-detail':
                toggleRowDetail(target);
                break;
            case 'refresh-log':
                refreshLogs(target);
                break;
            case 'reload-beets-config':
                reloadBeetsConfig(target);
                break;
            case 'save-beets-config':
                saveBeetsConfig(target);
                break;
            case 'test-connection':
                testConnection(target);
                break;
            case 'discover-devices':
                discoverDevices(target);
                break;
            case 'discover-radios':
                searchRadioStations(target);
                break;
            case 'use-radio-result':
                useRadioResult(target);
                break;
            case 'discover-podcasts':
                searchPodcasts(target);
                break;
            case 'use-podcast-result':
                usePodcastResult(target);
                break;
            case 'pick-radio-icon':
                const iconInput = document.getElementById(target.dataset.target);
                if (iconInput) iconInput.click();
                break;
            case 'pick-file':
                const fileInput = document.getElementById(target.dataset.target);
                if (fileInput) fileInput.click();
                break;
            case 'toggle-theme':
                toggleTheme();
                break;
            case 'confirm-proceed':
                settleConfirm(true);
                closeModal('confirmModal');
                break;
            case 'prompt-proceed':
                submitPrompt();
                break;
            case 'edit-chat':
                const msgId = target.dataset.id;
                const oldText = target.dataset.text;
                promptModal("Edit user's chat message:", oldText).then(newText => {
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
                });
                break;
        }
    });

    document.addEventListener('keydown', event => {
        if (event.key === 'Enter' && event.target.id === 'radioDiscoveryQuery') {
            event.preventDefault();
            const button = document.querySelector('[data-action="discover-radios"]');
            if (button) searchRadioStations(button);
        }
        if (event.key === 'Enter' && event.target.id === 'podcastDiscoveryQuery') {
            event.preventDefault();
            const button = document.querySelector('[data-action="discover-podcasts"]');
            if (button) searchPodcasts(button);
        }
    });

    document.addEventListener('change', event => {
        const target = event.target.closest('[data-action="toggle-log-autorefresh"]');
        if (target) toggleLogAutoRefresh(target);

        if (event.target.id === 'createRadioImage') {
            previewLocalRadioIcon(event.target, 'createRadioIconPreview', 'createRadioFavicon');
        }

        if (event.target.id === 'podcastOpmlFile' && event.target.files.length) {
            event.target.form.submit();
        }

        if (event.target.id === 'editRadioImage') {
            previewLocalRadioIcon(event.target, 'editRadioImagePreview', null);
            const removeCheckbox = document.getElementById('editRadioRemoveImage');
            if (removeCheckbox && event.target.files.length) removeCheckbox.checked = false;
        }

        if (event.target.id === 'set-jukebox_backend') {
            const deviceInput = document.getElementById('set-jukebox_hardware_device');
            if (deviceInput && deviceInput.value) deviceInput.value = '';

            const datalist = document.getElementById('jukebox-devices-list');
            if (datalist) datalist.innerHTML = '';

            const result = document.getElementById('test-result-jukebox_hardware_device');
            if (result) { result.className = 'test-result'; result.textContent = ''; }
        }
    });

    // Confirm dialogs
    document.addEventListener('submit', event => {
        const form = event.target.closest('form[data-confirm]');
        if (!form) return;
        event.preventDefault();
        confirmModal(form.dataset.confirm).then(ok => {
            if (ok) form.submit();
        });
    });

    // Roughly same input validation as the server does, just to know "this'll get rejected" or not
    function looksLikeIpOrCidr(s) {
        const ipv4 = /^(\d{1,3})(\.\d{1,3}){3}(\/\d{1,2})?$/;
        if (ipv4.test(s)) {
            return s.split('/')[0].split('.').every(o => Number(o) >= 0 && Number(o) <= 255);
        }
        return s.includes(':') && /^[0-9a-fA-F:]+(\/\d{1,3})?$/.test(s);
    }

    // Warn before adding the first whitelist entry/entries if current IP not in there
    document.addEventListener('submit', event => {
        const form = event.target.closest('form.ip-add-form[data-list-type="whitelist"][data-first-entry="true"]');
        if (!form) return;

        const ipInput = form.querySelector('input[name="ip"]');
        const raw = ipInput ? ipInput.value : '';
        const clientIp = form.dataset.clientIp || '';

        const entries = raw.split(',').map(s => s.trim()).filter(Boolean).filter(looksLikeIpOrCidr);
        if (entries.length === 0) return;          // garbage, let server reject it
        if (entries.includes(clientIp)) return;    // current IP is one of the entries: no problemo

        event.preventDefault();
        const listed = entries.map(e => `'${e}'`).join(', ');
        confirmModal(
            `Warning: Your current IP (${clientIp || 'unknown'}) is NOT ${entries.length === 1 ? "" : "listed in"} ${listed}. ` +
            `\n\nAccess will be restricted to ${entries.length === 1 ? "that IP" : "these IPs"} and the current IP will lose access immediately.\n\nContinue?`
        ).then(ok => {
            if (ok) form.submit();
        });
    });

    document.addEventListener('keydown', event => {
        // Enter submits
        if (event.key === 'Enter' && event.target.id === 'promptInput') {
            event.preventDefault();
            submitPrompt();
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

    const healthStatusRefreshBtn = document.querySelector('[data-action="refresh-health-status"]');
    if (healthStatusRefreshBtn) refreshHealthStatus(healthStatusRefreshBtn);

    startPodcastPollingIfBusy();

    document.querySelectorAll('details.podcast-episodes').forEach(details => {
        details.addEventListener('toggle', () => {
            if (details.open) loadPodcastEpisodes(details);
        });
    });

    document.querySelectorAll('[data-action="refresh-log"]').forEach(refreshLogs);

    // Format HLS/chat epoch timestamps to human readable format
    document.querySelectorAll('.chat-time').forEach(el => {
        const ms = parseInt(el.dataset.timestamp);
        if (!isNaN(ms)) {
            el.textContent = new Date(ms).toLocaleString();
        }
    });

    document.querySelectorAll('.config-time').forEach(formatConfigTime);

    // Tab key insert a tab instead of moving focus
    document.querySelectorAll('.code-editor').forEach(editor => {
        editor.addEventListener('keydown', event => {
            if (event.key !== 'Tab' || editor.readOnly) return;
            event.preventDefault();
            const start = editor.selectionStart, end = editor.selectionEnd;
            editor.value = editor.value.slice(0, start) + '  ' + editor.value.slice(end);
            editor.selectionStart = editor.selectionEnd = start + 2;
        });
    });

    // Auto-show the one-time API key modal if the server rendered one
    // Not dismissed by backdrop click just to be sure
    document.querySelectorAll('.modal-overlay[data-autoshow]').forEach(m => {
        m.classList.add('active');
    });
})();
