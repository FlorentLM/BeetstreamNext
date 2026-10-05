(function () {
    'use strict';

    // Alpine components (tabs, checkbox-group toggles)
    // (registered on 'alpine:init' so they exist before CSP build parses x-data)

    // Artist image modal
    document.body.addEventListener('htmx:after:swap', event => {
        const target = event.detail.ctx.target;
        const store = Alpine.store('modal');
        if (target?.id === 'artistImageModalBody') store.show('artistImageModal');
        else if (target?.id === 'artistImageList' && target.querySelector('.flash-success')
            && store.current === 'artistImageModal') {
            store.hide('artistImageModal');
            document.getElementById('artistImageModalBody').innerHTML = '';     // no stale preview next time
        }
    });

    document.addEventListener('alpine:init', () => {
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

        // Notification card

        Alpine.data('notificationCard', () => {
            const clearResult = card => card.querySelector('.notification-result')?.replaceChildren();

            return {
                dirty: false,

                markDirty(event) {
                    this.dirty = true;
                    clearResult(event.currentTarget);
                },

                // Cancel on a never-saved notification -> delete
                discard(event) {
                    event.currentTarget.closest('.notification-row').remove();
                },

                // Keep one (empty) row so there is always somewhere to type
                removeUrl(event) {
                    const row = event.currentTarget.closest('.notification-url-row');
                    const card = row.closest('.notification-card');
                    if (row.parentElement.querySelectorAll('.notification-url-row').length > 1) row.remove();
                    else row.querySelector('input').value = '';
                    this.dirty = true;
                    clearResult(card);
                }
            };
        });

        // Beets import panel: status/log pushed over the admin SSE stream (with one initial poll on page load)
        Alpine.data('beetsImport', () => {
            const LABELS = {
                idle: 'Idle', running: 'Running…', needs_input: 'Waiting for input',
                completed: 'Completed', failed: 'Failed'
            };

            async function post(url, body) {
                const csrfInput = document.querySelector('input[name="csrf_token"]');
                const resp = await fetch(url, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'X-CSRFToken': csrfInput ? csrfInput.value : ''
                    },
                    body: new URLSearchParams(body)
                });
                return resp.json();
            }

            return {
                state: 'idle',
                statusPath: '',
                starting: false,
                startMessage: '',
                startOk: true,

                get live() {
                    return this.state === 'running' || this.state === 'needs_input';
                },
                get label() {
                    if (this.live && this.statusPath) {
                        return (this.state === 'running' ? 'Importing ' : 'Waiting for input on ') + this.statusPath;
                    }
                    return LABELS[this.state] || this.state;
                },
                get stdinPlaceholder() {
                    return this.live ? 'reply to beets…' : 'beets is idle';
                },
                get startDisabled() {
                    return this.starting;
                },
                get startResultClass() {
                    if (!this.startMessage) return 'test-result';
                    return 'test-result ' + (this.startOk ? 'test-result-ok' : 'test-result-fail');
                },

                init() {
                    const dash = document.getElementById('adminDashboard');
                    if (dash) dash.addEventListener('beets-import', event => this.onSse(event));

                    const d = this.$el.dataset;
                    if (d.statusUrl) {
                        fetch(d.statusUrl, {credentials: 'same-origin'})
                            .then(resp => resp.ok ? resp.json() : null)
                            .then(status => { if (status) this.applyStatus(status); })
                            .catch(() => {});
                    }
                    if (d.logUrl) {
                        refreshLogs({dataset: {url: d.logUrl, target: 'beetsImportLogText'}});
                    }
                },

                applyStatus(status) {
                    this.state = status.state;
                    this.statusPath = status.path || '';
                },

                applyLines(lines) {
                    const target = this.$refs.log;
                    const wasAtBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 20;
                    target.innerHTML = lines.length ? lines.join('\n') : '(no import run yet)';
                    if (wasAtBottom) target.scrollTop = target.scrollHeight;
                },

                onSse(event) {
                    try {
                        const payload = JSON.parse(event.detail.data);
                        this.applyStatus(payload);
                        this.applyLines(payload.lines || []);
                    } catch (err) {
                        // Malformed payload, next event will hopefully replace
                    }
                },

                async start() {
                    const input = this.$refs.path;
                    const path = input.value.trim();
                    if (!path || this.starting) return;

                    this.starting = true;
                    try {
                        const payload = await post(this.$el.dataset.startUrl, {path});
                        this.startOk = !!payload.ok;
                        this.startMessage = payload.ok ? '' : payload.message;
                        this.applyStatus(payload);
                        if (this.live) input.value = '';
                    } catch (err) {
                        this.startOk = false;
                        this.startMessage = 'Failed to start: ' + err.message;
                    } finally {
                        this.starting = false;
                    }
                },

                async sendInput() {
                    const input = this.$refs.stdin;
                    if (!this.live) return;
                    const text = input.value;
                    input.value = '';
                    try {
                        this.applyStatus(await post(this.$el.dataset.inputUrl, {text}));
                    } catch (err) {
                        // next SSE push will hopefully correct the view
                    }
                    input.focus();
                }
            };
        });

        Alpine.data('checkboxGroup', () => ({
            setAll(checked, skip = []) {
                this.$root.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                    if (checked && skip.includes(cb.name)) return;
                    cb.checked = checked;
                });
            }
        }));
    });

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

        bsnCommon.copyText(text, done);
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

    // Podcast channel/episode status
    function formatBytes(bytes) {
        if (bytes >= 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        if (bytes >= 1024) return Math.round(bytes / 1024) + ' KB';
        return bytes + ' B';
    }

    function applyPodcastStatus(data) {
        for (const [id, info] of Object.entries(data.channels || {})) {
            const badge = document.getElementById(`podcast-channel-status-${id}`);
            if (badge && (badge.dataset.status !== info.status || badge.dataset.label !== info.status_label)) {
                badge.textContent = info.status_label || info.status;
                badge.dataset.status = info.status;
                badge.dataset.label = info.status_label || info.status;
                badge.title = info.error_message || '';
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
                badge.textContent = info.status_label || info.status;
                badge.dataset.status = info.status;
                badge.dataset.label = info.status_label || info.status;
                badge.title = info.error_message || '';
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


    const adminDashboard = document.getElementById('adminDashboard');
    adminDashboard?.addEventListener('podcast-status', handlePodcastStatusSse);
    adminDashboard?.addEventListener('server-log', handleServerLogSse);

    // Episodes lists are lazy-loaded htmx
    document.body.addEventListener('htmx:after:swap', event => {
        const target = event.detail.ctx.target;
        if (!target?.closest('.podcast-episodes')) return;
        syncPodcastStatuses();
    });

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

    // Clear feed URL box after successful subscribe
    document.body.addEventListener('htmx:after:swap', event => {
        const target = event.detail.ctx.target;
        if (target?.id !== 'podcastChannels') return;
        if (target.querySelector('.flash-success')) document.getElementById('addPodcastForm')?.reset();
    });

    // Modal forms swap the list behind them on success
    const MODAL_FORMS = {
        usersTable: [['createModal', 'createForm'], ['editModal', null]],
    };
    document.body.addEventListener('htmx:after:swap', event => {
        const target = event.detail.ctx.target;
        const modals = MODAL_FORMS[target?.id];
        if (!modals || !target.querySelector('.flash-success')) return;
        const store = Alpine.store('modal');
        for (const [modalId, formId] of modals) {
            if (store.current !== modalId) continue;
            const form = formId && document.getElementById(formId);
            if (form) {
                form.reset();
                form.querySelector('.modal-result').innerHTML = '';
            }
            store.hide(modalId);
        }
    });

    // Clear announcement box after successful post
    document.body.addEventListener('htmx:after:swap', event => {
        const target = event.detail.ctx.target;
        if (target?.id !== 'chatModeration') return;
        if (target.querySelector('.flash-success')) document.getElementById('announceForm')?.reset();
    });

    // Edit user forms, lazy-loaded
    document.body.addEventListener('htmx:after:swap', event => {
        const id = event.detail.ctx.target?.id;
        if (id === 'editModalBody') Alpine.store('modal').show('editModal');
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

    // Events

    document.addEventListener('click', event => {
        const target = event.target.closest('[data-action]');
        if (!target) return;

        switch (target.dataset.action) {
            case 'copy-log':
                copyLogs(target);
                break;
            case 'use-podcast-result':
                usePodcastResult(target);
                break;
            case 'use-beets-path-result':
                useBeetsPathResult(target);
                break;
            case 'edit-chat':
                const oldText = target.dataset.text;
                Alpine.store('modal').prompt("Edit user's chat message:", oldText).then(newText => {
                    if (newText !== null && newText.trim() !== "") {
                        htmx.ajax('post', target.dataset.url, {
                            target: '#chatModeration',
                            swap: 'innerHTML',
                            values: {message: newText},
                        });
                    }
                });
                break;
        }
    });

    const beetsPathResults = document.getElementById('beetsImportPathResults');
    if (beetsPathResults) {
        beetsPathResults.addEventListener('mouseleave', () => {
            beetsPathResults.innerHTML = '';
        });
    }

    document.addEventListener('change', event => {
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
        event.stopPropagation();   // Keep htmx from sending the unconfirmed submit
        const listed = entries.map(e => `'${e}'`).join(', ');
        Alpine.store('modal').confirm(
            `Warning: Your current IP (${clientIp || 'unknown'}) is NOT ${entries.length === 1 ? "" : "listed in"} ${listed}. ` +
            `\n\nAccess will be restricted to ${entries.length === 1 ? "that IP" : "these IPs"} and the current IP will lose access immediately.\n\nContinue?`
        ).then(ok => {
            if (ok) htmx.trigger(form, 'confirmed');
        });
    }, true);

    // Init


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
