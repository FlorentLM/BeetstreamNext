(function () {
    'use strict';

    const {onSwap, onSwapSuccess} = bsnCommon;

    // Artist image modal
    onSwap('artistImageModalBody', () => Alpine.store('modal').show('artistImageModal'));
    onSwapSuccess('artistImageList', () => {
        const store = Alpine.store('modal');
        if (store.current !== 'artistImageModal') return;
        store.hide('artistImageModal');
        document.getElementById('artistImageModalBody').innerHTML = '';     // no stale preview next time
    });

    // Alpine components (tabs, notification cards, beets import, checkbox-group toggles)
    // (registered on 'alpine:init' so they exist before CSP build parses x-data)
    document.addEventListener('alpine:init', () => {
        Alpine.data('tabs', () => ({
            active: 'users',
            validTabs: [],
            menuOpen: false,

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

            pickTab(name) {
                this.set(name);
                this.menuOpen = false;
            },

            toggleMenu() {
                this.menuOpen = !this.menuOpen;
            },

            closeMenu() {
                this.menuOpen = false;
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
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                return resp.json();
            }

            return {
                state: 'idle',
                statusPath: '',
                starting: false,
                startMessage: '',
                startOk: true,
                inputMessage: '',
                inputOk: true,
                startUrl: '',       // inside a handler, $el is what fired the event
                inputUrl: '',

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
                get inputResultClass() {
                    if (!this.inputMessage) return 'test-result';
                    return 'test-result ' + (this.inputOk ? 'test-result-ok' : 'test-result-fail');
                },
                get startResultClass() {
                    if (!this.startMessage) return 'test-result';
                    return 'test-result ' + (this.startOk ? 'test-result-ok' : 'test-result-fail');
                },

                init() {
                    const dash = document.getElementById('adminDashboard');
                    if (dash) dash.addEventListener('beets-import', event => this.onSse(event));

                    const d = this.$el.dataset;
                    this.startUrl = d.startUrl;
                    this.inputUrl = d.inputUrl;
                    if (d.statusUrl) {
                        fetch(d.statusUrl, {credentials: 'same-origin'})
                            .then(resp => resp.ok ? resp.json() : null)
                            .then(status => {
                                if (status) this.applyStatus(status);
                            })
                            .catch(() => {
                            });
                    }
                    if (d.logUrl) {
                        refreshLogs(d.logUrl, 'beetsImportLogText');
                    }
                },

                applyStatus(status) {
                    this.state = status.state;
                    this.statusPath = status.path || '';
                },

                applyLines(lines) {
                    const target = this.$refs.log;
                    const wasAtBottom = target.scrollHeight - target.scrollTop - target.clientHeight < 20;
                    target.innerHTML = lines.length ? lines.join('\n') : (this.live ? 'Running import…' : '(no import run yet)');
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
                        const payload = await post(this.startUrl, {path});
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
                        const payload = await post(this.inputUrl, {text});
                        this.applyStatus(payload);
                        this.inputOk = !!payload.ok;
                        this.inputMessage = payload.ok ? '' : payload.message;
                        if (!payload.ok && !input.value) input.value = text;
                    } catch (err) {
                        // Not sent: give the text back (unless the user already started typing something else)
                        this.inputOk = false;
                        this.inputMessage = 'Could not send: ' + err.message;
                        if (!input.value) input.value = text;
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


    const logRefreshes = new Map();

    async function refreshLogs(url, targetId) {
        const target = document.getElementById(targetId);
        if (!target || !url) return;
        if (logRefreshes.has(targetId)) {
            logRefreshes.get(targetId).again = true;
            return;
        }
        const state = {again: false};
        logRefreshes.set(targetId, state);
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
        } finally {
            logRefreshes.delete(targetId);
            if (state.again) refreshLogs(url, targetId);
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
        if (count >= SERVER_LOG_MAX_LINES || logRefreshes.has(target.id)) {
            refreshLogs(target.dataset.url, target.id);
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
                    const sizeBadge = document.createElement('span');
                    sizeBadge.className = 'badge badge-limit';
                    sizeBadge.textContent = formatBytes(info.file_size);
                    sizeEl.replaceChildren(sizeBadge);
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

    onSwap('beetsConfigEditorWrap', () => {
        const footer = document.getElementById('beetsConfigFooter');
        if (footer) footer.querySelectorAll('.config-time').forEach(formatConfigTime);
    });

    // only fill the field when it's empty and discovery found exactly one device
    onSwap('jukebox-devices-list', target => {
        const input = document.getElementById('set-jukebox_hardware_device');
        const options = target.querySelectorAll('option');
        if (input && !input.value && options.length === 1) {
            input.value = options[0].value;
        }
    });

    // Clear feed URL box after successful subscribe
    onSwapSuccess('podcastChannels', () => document.getElementById('addPodcastForm')?.reset());

    bsnCommon.closeModalsOnSuccess({
        usersTable: [['createModal', 'createForm'], ['editModal', null]],
    });

    // Clear announcement box after successful post
    onSwapSuccess('chatModeration', () => document.getElementById('announceForm')?.reset());

    // Edit user forms, lazy-loaded
    onSwap('editModalBody', () => Alpine.store('modal').show('editModal'));

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
            case 'use-beets-path-result':
                useBeetsPathResult(target);
                break;
            case 'edit-chat': {
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

    // IPv4/IPv6 text -> {bits, n}, null if unparsable
    function parseIp(s) {
        if (!s.includes(':')) {
            const octets = s.split('.');
            if (octets.length !== 4 || !octets.every(o => /^\d{1,3}$/.test(o) && Number(o) <= 255)) return null;
            return {bits: 32, n: octets.reduce((acc, o) => (acc << 8n) | BigInt(o), 0n)};
        }
        const halves = s.split('::');
        if (halves.length > 2) return null;
        const head = halves[0] ? halves[0].split(':') : [];
        const tail = halves.length === 2 && halves[1] ? halves[1].split(':') : [];
        const fill = 8 - head.length - tail.length;
        if (halves.length === 1 ? fill !== 0 : fill < 1) return null;   // '::' stands for at least one group
        const groups = [...head, ...Array(Math.max(fill, 0)).fill('0'), ...tail];
        if (!groups.every(g => /^[0-9a-f]{1,4}$/i.test(g))) return null;
        return {bits: 128, n: groups.reduce((acc, g) => (acc << 16n) | BigInt(parseInt(g, 16)), 0n)};
    }

    // Is the IP inside the entry (a single address or a CIDR range)?
    function entryCovers(entry, ip) {
        const [addr, prefixStr] = entry.split('/');
        const net = parseIp(addr), client = parseIp(ip);
        if (!net || !client || net.bits !== client.bits) return false;
        const prefix = prefixStr === undefined ? net.bits : Number(prefixStr);
        if (prefix > net.bits) return false;
        const shift = BigInt(net.bits - prefix);
        return (net.n >> shift) === (client.n >> shift);
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
        if (entries.some(e => entryCovers(e, clientIp))) return;    // current IP is inside one of the entries: no problemo

        event.preventDefault();
        event.stopPropagation();   // Keep htmx from sending the unconfirmed submit
        const listed = entries.map(e => `'${e}'`).join(', ');
        Alpine.store('modal').confirm(
            `Warning: Your current IP (${clientIp || 'unknown'}) is NOT covered by ${listed}. ` +
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
