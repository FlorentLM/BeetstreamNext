(function () {
    'use strict';

    // Alpine components (tabs, modals, checkbox-group toggles)
    // (registered on 'alpine:init' so they exist before CSP build parses x-data)

    document.addEventListener('alpine:init', () => {
        Alpine.store('modal', {
            current: null,
            message: '',
            promptValue: '',
            confirmResolve: null,
            promptResolve: null,

            show(id) {
                this.current = id;
            },

            hide(id) {
                if (id && this.current !== id) return;
                this.current = null;
                this.settleConfirm(false);
                this.settlePrompt(null);
            },

            settleConfirm(result) {
                if (!this.confirmResolve) return;
                const resolve = this.confirmResolve;
                this.confirmResolve = null;
                resolve(result);
            },

            settlePrompt(result) {
                if (!this.promptResolve) return;
                const resolve = this.promptResolve;
                this.promptResolve = null;
                resolve(result);
            },

            confirm(message) {
                return new Promise(resolve => {
                    this.confirmResolve = resolve;
                    this.message = message;
                    this.show('confirmModal');
                });
            },

            prompt(message, defaultValue) {
                return new Promise(resolve => {
                    this.promptResolve = resolve;
                    this.message = message;
                    this.promptValue = defaultValue || '';
                    this.show('promptModal');
                    setTimeout(() => {
                        const input = document.getElementById('promptInput');
                        if (input) {
                            input.focus();
                            input.select();
                        }
                    }, 50);
                });
            },

            confirmProceed() {
                this.settleConfirm(true);
                this.hide('confirmModal');
            },

            promptProceed() {
                this.settlePrompt(this.promptValue);
                this.hide('promptModal');
            }
        });

        Alpine.data('tabs', () => ({
            active: 'users',
            validTabs: [],

            init() {
                this.validTabs = Array.from(this.$el.querySelectorAll('.tab[data-tab]')).map(t => t.dataset.tab);
                this.syncFromHash();
                window.addEventListener('hashchange', () => this.syncFromHash());
            },

            syncFromHash() {
                const hash = (window.location.hash || '').slice(1);
                this.active = this.validTabs.includes(hash) ? hash : (this.validTabs[0] || 'users');
            },

            set(name) {
                this.active = name;
                history.replaceState(null, '', '#' + name);
            },

            closeModal() {
                Alpine.store('modal').hide();
            }
        }));

        // Routing modal state via getters bcause the CSP build doesn't resolve the nested $store
        Alpine.data('modalPanel', (id) => ({
            get isOpen() {
                return Alpine.store('modal').current === id;
            },
            get message() {
                return Alpine.store('modal').message;
            },
            get promptValue() {
                return Alpine.store('modal').promptValue;
            },
            set promptValue(v) {
                Alpine.store('modal').promptValue = v;
            },
            open() {
                Alpine.store('modal').show(id);
            },
            close() {
                Alpine.store('modal').hide(id);
            },
            confirmProceed() {
                Alpine.store('modal').confirmProceed();
            },
            promptProceed() {
                Alpine.store('modal').promptProceed();
            }
        }));

        Alpine.data('checkboxGroup', () => ({
            setAll(checked, skip = []) {
                this.$el.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                    if (checked && skip.includes(cb.name)) return;
                    cb.checked = checked;
                });
            }
        }));
    });

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
        } catch (e) {
        }
        applyTheme(next);
    }


    // One-time API key copy

    function copyApiKey(button) {
        const el = document.getElementById('apiKeyValue');
        if (!el) return;
        const key = el.textContent.trim();

        const done = () => {
            button.textContent = 'Copied';
            setTimeout(() => {
                button.textContent = 'Copy';
            }, 2000);
        };

        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(key).then(done).catch(() => {
                if (copySel(key)) done();
            });
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
            setTimeout(() => {
                label.textContent = orig;
            }, 2000);
        };

        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(done).catch(() => {
                if (copySel(text)) done();
            });
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
            if (openSpan) {
                html += '</span>';
                openSpan = false;
            }
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
                    if (code === 0) {
                        colorClass = null;
                        bold = dim = italic = underline = false;
                    } else if (code === 1) bold = true;
                    else if (code === 2) dim = true;
                    else if (code === 3) italic = true;
                    else if (code === 4) underline = true;
                    else if (code === 22) {
                        bold = false;
                        dim = false;
                    } else if (code === 23) italic = false;
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
            const resp = await fetch(url, {credentials: 'same-origin'});
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

    async function refreshScanStatus(button) {
        const url = button.dataset.url;
        const statusEl = document.getElementById('beets-scan-status-value');
        const countEl = document.getElementById('beets-scan-count-value');
        if (!url || !statusEl) return;
        try {
            const resp = await fetch(url, {credentials: 'same-origin'});
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
            const resp = await fetch(url, {credentials: 'same-origin'});
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
    let podcastPollInterval = null;

    function podcastSetPollInterval(ms) {
        if (podcastPollInterval === ms) return;
        if (podcastPollTimer) clearInterval(podcastPollTimer);
        podcastPollInterval = ms;
        podcastPollTimer = setInterval(refreshPodcastStatuses, ms);
    }

    async function refreshPodcastStatuses() {
        try {
            const resp = await fetch('/admin/podcasts/status', {credentials: 'same-origin'});
            if (!resp.ok) return;
            const data = await resp.json();
            let busy = false;
            let activelyDownloading = false;

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

                const progressEl = document.getElementById(`podcast-episode-progress-${id}`);
                if (progressEl) {
                    progressEl.hidden = info.status !== 'downloading';
                    if (info.status === 'downloading') {
                        if (info.file_size) {
                            progressEl.max = info.file_size;
                            progressEl.value = info.bytes_downloaded || 0;
                        } else {
                            progressEl.removeAttribute('max');
                            progressEl.removeAttribute('value');
                        }
                    }
                }

                if (info.status === 'downloading') {
                    busy = true;
                    activelyDownloading = true;
                }
            }

            if (!busy && podcastPollTimer) {
                clearInterval(podcastPollTimer);
                podcastPollTimer = null;
                podcastPollInterval = null;
            } else if (busy) {
                podcastSetPollInterval(activelyDownloading ? 1000 : 3000);
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
        if (!document.querySelector(busySelector)) return;
        const activelyDownloading = document.querySelector('[id^="podcast-episode-status-"][data-status="downloading"]');
        podcastSetPollInterval(activelyDownloading ? 1000 : 3000);
    }

    // Episodes lists are lazy-loaded htmx
    document.body.addEventListener('htmx:afterSwap', event => {
        if (!event.detail.target.closest('.podcast-episodes')) return;
        formatChatTimes(event.detail.target);
        startPodcastPollingIfBusy();
    });

    // Format HLS/chat/podcast epoch timestamps to human readable format
    function formatChatTimes(root = document) {
        root.querySelectorAll('.chat-time').forEach(el => {
            const ms = parseInt(el.dataset.timestamp);
            if (!isNaN(ms)) {
                el.textContent = new Date(ms).toLocaleString();
            }
        });
    }

    // Beets config editor "last read at"

    function formatConfigTime(el) {
        const ms = parseInt(el.dataset.timestamp);
        el.textContent = isNaN(ms) ? '—' : new Date(ms).toLocaleTimeString();
    }

    document.body.addEventListener('htmx:oobAfterSwap', event => {
        if (event.detail.target.id !== 'beetsConfigFooter') return;
        const footer = document.getElementById('beetsConfigFooter');
        if (footer) footer.querySelectorAll('.config-time').forEach(formatConfigTime);
    });

    // htmx requests are same-origin -> attach the CSRF token to every one
    document.body.addEventListener('htmx:configRequest', event => {
        const csrfInput = document.querySelector('input[name="csrf_token"]');
        if (csrfInput) event.detail.headers['X-CSRFToken'] = csrfInput.value;
    });

    // only fill the field when it's empty and discovery found exactly one device
    document.body.addEventListener('htmx:afterSwap', event => {
        if (event.detail.target.id !== 'jukebox-devices-list') return;
        const input = document.getElementById('set-jukebox_hardware_device');
        const options = event.detail.target.querySelectorAll('option');
        if (input && !input.value && options.length === 1) {
            input.value = options[0].value;
        }
    });

    // Edit user/radio forms, lazy-loaded
    document.body.addEventListener('htmx:afterSwap', event => {
        if (event.detail.target.id === 'editModalBody') Alpine.store('modal').show('editModal');
        if (event.detail.target.id === 'editRadioModalBody') Alpine.store('modal').show('editRadioModal');
    });

    function usePodcastResult(target) {
        const urlInput = document.getElementById('podcastFeedUrl');
        if (urlInput) urlInput.value = target.dataset.url || '';

        const results = document.getElementById('podcastDiscoveryResults');
        if (results) results.innerHTML = '';
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
        if (results) results.innerHTML = '';

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
        const params = new URLSearchParams({name});
        if (favicon) params.set('url', favicon);
        if (homepage) params.set('homepage', homepage);

        try {
            const resp = await fetch(`${proxyBase}?${params.toString()}`, {credentials: 'same-origin'});
            if (!resp.ok) return;
            const blob = await resp.blob();

            const blobUrl = URL.createObjectURL(blob);
            preview.dataset.blobUrl = blobUrl;
            preview.src = blobUrl;
            preview.classList.remove('hidden');

            if (imageInput && typeof DataTransfer !== 'undefined') {
                const file = new File([blob], 'icon', {type: blob.type || 'application/octet-stream'});
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
        const target = event.target.closest('[data-action]');
        if (!target) return;

        switch (target.dataset.action) {
            case 'copy-api-key':
                copyApiKey(target);
                break;
            case 'copy-log':
                copyLogs(target);
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
            case 'use-radio-result':
                useRadioResult(target);
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
            case 'edit-chat':
                const msgId = target.dataset.id;
                const oldText = target.dataset.text;
                Alpine.store('modal').prompt("Edit user's chat message:", oldText).then(newText => {
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

    // htmx's `keyup[key=='Enter']` trigger filters compile the condition with `new Function()`,
    // which our CSP blocks (no 'unsafe-eval'). Enter-to-search is wired up here instead.
    document.addEventListener('keydown', event => {
        if (event.key !== 'Enter') return;
        const input = event.target.closest('#podcastDiscoveryQuery, #radioDiscoveryQuery');
        if (!input) return;
        event.preventDefault();
        const button = input.parentElement.querySelector('button[hx-get]');
        if (button) button.click();
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
            if (result) {
                result.className = 'test-result';
                result.textContent = '';
            }
        }
    });

    // Confirm dialogs
    document.addEventListener('submit', event => {
        const form = event.target.closest('form[data-confirm]');
        if (!form) return;
        event.preventDefault();
        Alpine.store('modal').confirm(form.dataset.confirm).then(ok => {
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
        Alpine.store('modal').confirm(
            `Warning: Your current IP (${clientIp || 'unknown'}) is NOT ${entries.length === 1 ? "" : "listed in"} ${listed}. ` +
            `\n\nAccess will be restricted to ${entries.length === 1 ? "that IP" : "these IPs"} and the current IP will lose access immediately.\n\nContinue?`
        ).then(ok => {
            if (ok) form.submit();
        });
    });

    // Init

    applyTheme(document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark');

    const scanStatusRefreshBtn = document.querySelector('[data-action="refresh-scan-status"]');
    if (scanStatusRefreshBtn) refreshScanStatus(scanStatusRefreshBtn);

    const healthStatusRefreshBtn = document.querySelector('[data-action="refresh-health-status"]');
    if (healthStatusRefreshBtn) refreshHealthStatus(healthStatusRefreshBtn);

    startPodcastPollingIfBusy();

    document.querySelectorAll('[data-action="refresh-log"]').forEach(refreshLogs);

    formatChatTimes();

    document.querySelectorAll('.config-time').forEach(formatConfigTime);

    // Tab key insert a tab instead of moving focus
    document.addEventListener('keydown', event => {
        const editor = event.target.closest('.code-editor');
        if (!editor || event.key !== 'Tab' || editor.readOnly) return;
        event.preventDefault();
        const start = editor.selectionStart, end = editor.selectionEnd;
        editor.value = editor.value.slice(0, start) + '  ' + editor.value.slice(end);
        editor.selectionStart = editor.selectionEnd = start + 2;
    });
})();
