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
            target.innerHTML = lines.length ? lines.join('\n') : '(no log output yet)';
            target.dataset.lines = lines.length;
            if (wasAtBottom) target.scrollTop = target.scrollHeight;
        } catch (err) {
            target.textContent = 'Failed to load log: ' + err.message;
        }
    }


    const SERVER_LOG_MAX_LINES = 2500;   // server keeps 2000, so resync from it when the page has drifted too far

    function handleServerLogSse(event) {
        const target = document.getElementById('serverLogText');
        if (!target) return;

        let batch;
        try {
            batch = JSON.parse(event.detail.data);
        } catch (err) {
            return;   // Malformed payload, Refresh should resync
        }
        if (!Array.isArray(batch) || !batch.length) return;

        const count = parseInt(target.dataset.lines) || 0;
        if (count >= SERVER_LOG_MAX_LINES) {
            refreshLogs({dataset: {url: target.dataset.url, target: target.id}});
            return;
        }

        const wasAtBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 20;
        if (count === 0) {
            target.innerHTML = batch.join('\n');
        } else {
            target.insertAdjacentHTML('beforeend', '\n' + batch.join('\n'));
        }
        target.dataset.lines = count + batch.length;
        if (wasAtBottom) target.scrollTop = target.scrollHeight;
    }

    // Beets import

    const IMPORT_STATE_LABELS = {
        idle: 'Idle', running: 'Running…', needs_input: 'Waiting for input',
        completed: 'Completed', failed: 'Failed'
    };

    function applyImportStatus(status) {
        const statusEl = document.getElementById('beetsImportStatusValue');
        const pathEl = document.getElementById('beetsImportPathValue');
        const stdinInput = document.getElementById('beetsImportStdin');
        const sendBtn = document.querySelector('[data-action="send-beets-import-input"]');
        if (!statusEl) return false;

        statusEl.textContent = IMPORT_STATE_LABELS[status.state] || status.state;
        if (pathEl) pathEl.textContent = status.path || '—';

        const live = status.state === 'running' || status.state === 'needs_input';
        if (stdinInput) {
            stdinInput.disabled = !live;
            stdinInput.placeholder = live ? 'reply to beets…' : 'beets is idle';
        }
        if (sendBtn) sendBtn.disabled = !live;

        return live;
    }

    function applyImportLines(lines) {
        const target = document.getElementById('beetsImportLogText');
        if (!target) return;
        const wasAtBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 20;
        target.innerHTML = lines.length ? lines.join('\n') : '(no import run yet)';
        if (wasAtBottom) target.scrollTop = target.scrollHeight;
    }

    function handleBeetsImportSse(event) {
        try {
            const payload = JSON.parse(event.detail.data);
            applyImportStatus(payload);
            applyImportLines(payload.lines || []);
        } catch (err) {
            // Malformed payload, next event will hopefully replace
        }
    }


    const adminSseSource = document.getElementById('adminDashboard');
    if (adminSseSource) adminSseSource.addEventListener('beets-import', handleBeetsImportSse);

    async function startBeetsImport(button) {
        const url = button.dataset.url;
        const pathInput = document.getElementById('beetsImportPath');
        const result = document.getElementById('beetsImportStartResult');

        if (!url || !pathInput) return;
        const path = pathInput.value.trim();
        if (!path) return;

        button.disabled = true;
        try {
            const csrfInput = document.querySelector('input[name="csrf_token"]');
            const resp = await fetch(url, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRFToken': csrfInput ? csrfInput.value : ''
                },
                body: new URLSearchParams({path})
            });

            const payload = await resp.json();
            if (result) {
                result.className = 'test-result ' + (payload.ok ? 'test-result-ok' : 'test-result-fail');
                result.textContent = payload.ok ? '' : payload.message;
            }

            const live = applyImportStatus(payload);
            if (live) pathInput.value = '';

        } catch (err) {
            if (result) {
                result.className = 'test-result test-result-fail';
                result.textContent = 'Failed to start: ' + err.message;
            }
        } finally {
            button.disabled = false;
        }
    }

    async function sendBeetsImportInput(button) {
        const url = button.dataset.url;
        const input = document.getElementById('beetsImportStdin');

        if (!url || !input || input.disabled) return;
        const text = input.value;
        input.value = '';

        const csrfInput = document.querySelector('input[name="csrf_token"]');
        try {
            const resp = await fetch(url, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRFToken': csrfInput ? csrfInput.value : ''
                },
                body: new URLSearchParams({text})
            });
            applyImportStatus(await resp.json());
        } catch (err) {
            // next SSE push will hopefully correct the view
        }
        input.focus();
    }

    // Podcast channel/episode status
    function formatBytes(bytes) {
        if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        if (bytes >= 1024) return Math.round(bytes / 1024) + ' KB';
        return bytes + ' B';
    }

    function applyPodcastStatus(data) {
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
        }
    }

    function handlePodcastStatusSse(event) {
        try {
            applyPodcastStatus(JSON.parse(event.detail.data));
        } catch (err) {
            // Malformed payload, next event will hopefully replace
        }
    }


    async function syncPodcastStatuses() {
        try {
            const resp = await fetch('/admin/podcasts/status', {credentials: 'same-origin'});
            if (resp.ok) applyPodcastStatus(await resp.json());
        } catch (err) {
            // next SSE push will correct the view
        }
    }

    if (adminSseSource) adminSseSource.addEventListener('podcast-status', handlePodcastStatusSse);
    if (adminSseSource) adminSseSource.addEventListener('server-log', handleServerLogSse);

    // Episodes lists are lazy-loaded htmx
    document.body.addEventListener('htmx:after:swap', event => {
        const target = event.detail.ctx.target;
        if (!target?.closest('.podcast-episodes')) return;
        formatChatTimes(target);
        syncPodcastStatuses();
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

    document.body.addEventListener('htmx:after:swap', event => {
        if (event.detail.ctx.target?.id !== 'beetsConfigEditorWrap') return;
        const footer = document.getElementById('beetsConfigFooter');
        if (footer) footer.querySelectorAll('.config-time').forEach(formatConfigTime);
    });

    // htmx requests are same-origin -> attach the CSRF token to every one
    document.body.addEventListener('htmx:config:request', event => {
        const csrfInput = document.querySelector('input[name="csrf_token"]');
        if (csrfInput) event.detail.ctx.request.headers['X-CSRFToken'] = csrfInput.value;
    });

    // only fill the field when it's empty and discovery found exactly one device
    document.body.addEventListener('htmx:after:swap', event => {
        const target = event.detail.ctx.target;
        if (target?.id !== 'jukebox-devices-list') return;
        const input = document.getElementById('set-jukebox_hardware_device');
        const options = target.querySelectorAll('option');
        if (input && !input.value && options.length === 1) {
            input.value = options[0].value;
        }
    });

    // Edit user/radio forms, lazy-loaded
    document.body.addEventListener('htmx:after:swap', event => {
        const id = event.detail.ctx.target?.id;
        if (id === 'editModalBody') Alpine.store('modal').show('editModal');
        if (id === 'editRadioModalBody') Alpine.store('modal').show('editRadioModal');
    });

    function usePodcastResult(target) {
        const urlInput = document.getElementById('podcastFeedUrl');
        if (urlInput) urlInput.value = target.dataset.url || '';

        const results = document.getElementById('podcastDiscoveryResults');
        if (results) results.innerHTML = '';
    }

    function useBeetsPathResult(target) {
        const results = document.getElementById('beetsImportPathResults');
        if (results) results.innerHTML = '';

        const input = document.getElementById('beetsImportPath');
        if (!input) return;

        const path = (target.dataset.path || '') + '/';
        input.value = path;
        input.focus();

        const url = input.getAttribute('hx-get');
        if (url && window.htmx) {
            htmx.ajax('GET', url, {source: input, target: '#beetsImportPathResults', values: {path}});
        }
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
            case 'start-beets-import':
                startBeetsImport(target);
                break;
            case 'send-beets-import-input':
                sendBeetsImportInput(target);
                break;
            case 'use-radio-result':
                useRadioResult(target);
                break;
            case 'use-podcast-result':
                usePodcastResult(target);
                break;
            case 'use-beets-path-result':
                useBeetsPathResult(target);
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

    // Enter to submit
    document.addEventListener('keydown', event => {
        if (event.key !== 'Enter') return;

        if (event.target.id === 'beetsImportPath') {
            event.preventDefault();
            const button = document.querySelector('[data-action="start-beets-import"]');
            if (button) button.click();
        } else if (event.target.id === 'beetsImportStdin') {
            event.preventDefault();
            const button = document.querySelector('[data-action="send-beets-import-input"]');
            if (button && !button.disabled) button.click();
        }
    });

    const beetsPathResults = document.getElementById('beetsImportPathResults');
    if (beetsPathResults) {
        beetsPathResults.addEventListener('mouseleave', () => {
            beetsPathResults.innerHTML = '';
        });
    }

    document.addEventListener('change', event => {
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


    // One initial poll, then live updates on SSE connection
    const startImportBtn = document.querySelector('[data-action="start-beets-import"]');
    if (startImportBtn && startImportBtn.dataset.statusUrl) {
        fetch(startImportBtn.dataset.statusUrl, {credentials: 'same-origin'})
            .then(resp => resp.ok ? resp.json() : null)
            .then(status => { if (status) applyImportStatus(status); })
            .catch(() => {});
        if (startImportBtn.dataset.logUrl) {
            refreshLogs({dataset: {url: startImportBtn.dataset.logUrl, target: 'beetsImportLogText'}});
        }
    }

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
