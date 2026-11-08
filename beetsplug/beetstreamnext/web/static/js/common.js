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
        document.cookie = 'bsn-theme=' + next + '; Path=/; Max-Age=31536000; SameSite=Lax';
        applyTheme(next);
    }

    applyTheme(document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark');

    // Clipboard

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

    // Copies text then calls done() on success
    function copyText(text, done) {
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(done, () => {
                if (copySel(text)) done();
            });
        } else {
            // No Clipboard API over plain HTTP, fallback to execCommand
            if (copySel(text)) done();
        }
    }

    // One-time API key copy

    function copyAppPassword(button) {
        const el = document.getElementById(button.dataset.target || 'apiKeyValue');
        if (!el) return;

        copyText(el.textContent.trim(), () => {
            button.textContent = 'Copied';
            setTimeout(() => {
                button.textContent = 'Copy';
            }, 2000);
        });
    }

    // Epoch timestamps (chat, podcast episodes, shares) in htmx-loaded partials

    function formatTimes(root = document) {
        root.querySelectorAll('.chat-time').forEach(el => {
            const ms = parseInt(el.dataset.timestamp);
            if (isNaN(ms)) return;
            const date = new Date(ms);
            el.title = date.toLocaleString();
            if (el.dataset.dateOnly) el.textContent = date.toLocaleDateString();
            else if (el.dataset.timeOnly) el.textContent = date.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});
            else el.textContent = date.toLocaleString();
        });
    }

    formatTimes();

    document.body.addEventListener('htmx:after:swap', () => formatTimes());

    // Tabulator tables

    let tabulatorLoading = null;

    function loadTabulator(el) {
        tabulatorLoading ??= new Promise((resolve, reject) => {

            const styles = [el.dataset.tableCss, el.dataset.tableTheme].map(href => {
                const link = document.createElement('link');
                link.rel = 'stylesheet';
                link.href = href;
                return link;
            });

            const script = document.createElement('script');
            script.src = el.dataset.tableJs;
            script.onload = resolve;
            script.onerror = () => {
                tabulatorLoading = null;
                reject(new Error('Could not load Tabulator'));
            };
            document.head.append(...styles, script);
        });
        return tabulatorLoading;
    }

    function htmlFormatter(key) {
        return (cell, params, onRendered) => {
            onRendered(() => window.htmx?.process(cell.getElement()));
            return cell.getData()[key] ?? '';
        };
    }

    // Album cover (or a colour block derived from the album name)
    function coverArt(track, template, size) {
        const block = () => {
            const span = document.createElement('span');
            span.className = 'track-art';
            const hue = [...(track.album || '')].reduce((n, c) => (n * 31 + c.charCodeAt(0)) % 360, 7);
            span.style.background = `hsl(${hue} 45% 38%)`;
            return span;
        };
        if (!track.album_id || !template) return block();
        const img = document.createElement('img');
        img.className = 'track-art';
        img.alt = '';
        img.loading = 'lazy';
        img.src = template.slice(0, -1) + track.album_id + '?size=' + size;
        img.addEventListener('error', () => img.replaceWith(block()), {once: true});
        return img;
    }

    const TABLE_COLUMN_TYPES = {
        badge: {
            formatter: (cell, params) => {
                const pill = document.createElement('span');
                pill.className = ('badge ' + (params?.cls ?? 'badge-admin')).trim();
                pill.textContent = cell.getValue();
                return pill;
            }
        },
        time: {
            sorter: 'number',
            formatter: (cell, params) => {
                const value = cell.getValue();
                if (!value) return params?.empty ?? '—';
                const date = new Date(value);
                return params?.dateOnly ? date.toLocaleDateString() : date.toLocaleString();
            }
        },
        number: {
            sorter: 'number'
        },
        length: {
            sorter: 'number',
            hozAlign: 'right',
            formatter: cell => {
                const secs = cell.getValue();
                return secs ? Math.floor(secs / 60) + ':' + String(Math.floor(secs % 60)).padStart(2, '0') : '—';
            }
        },
        // Track title + album cover
        track: {
            formatter: (cell, params) => {
                const track = cell.getData();
                const wrap = document.createElement('div');
                wrap.className = 'track-title';
                const text = document.createElement('span');
                text.textContent = track.title;
                wrap.append(coverArt(track, params?.art, 64), text);
                return wrap;
            }
        },
        actions: {
            headerSort: false,
            resizable: false,
            widthShrink: 0,
            hozAlign: 'right',
            cssClass: 'table-actions'
        }
    };

    // Per-column text filter
    function filterIcon() {
        const icon = document.createElement('span');
        icon.className = 'material-symbols-outlined table-filter-icon';
        icon.setAttribute('aria-hidden', 'true');
        icon.textContent = 'filter_list';
        return icon;
    }

    function filterPopup(e, column, onRendered) {
        const wrap = document.createElement('div');
        wrap.className = 'table-filter-popup';
        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = 'Filter ' + column.getDefinition().title.toLowerCase();
        input.value = column.getHeaderFilterValue() || '';
        let timer = null;
        input.addEventListener('input', () => {
            column.getElement().querySelector('.table-filter-icon')?.classList.toggle('active', input.value !== '');
            clearTimeout(timer);
            timer = setTimeout(() => column.setHeaderFilterValue(input.value), 300);
        });
        wrap.append(input);
        onRendered(() => input.focus());
        return wrap;
    }


    function selectPopup(values) {
        return (e, column, onRendered) => {
            const wrap = document.createElement('div');
            wrap.className = 'table-filter-popup';
            const select = document.createElement('select');
            select.append(new Option('All', ''));
            values.forEach(value => select.append(new Option(value, value)));
            select.value = column.getHeaderFilterValue() || '';
            select.addEventListener('change', () => {
                column.setHeaderFilterValue(select.value);
                column.getElement().querySelector('.table-filter-icon')?.classList.toggle('active', select.value !== '');
            });
            wrap.append(select);
            onRendered(() => select.focus());
            return wrap;
        };
    }

    function withFilter(col, rows) {
        if (col.filter === 'select') {
            const values = [...new Set(rows.map(row => row[col.field]).filter(v => v !== '' && v != null))].sort();
            return {
                ...col,
                headerPopup: selectPopup(values),
                headerPopupIcon: filterIcon,
                headerFilter: () => document.createElement('div'),
                headerFilterFunc: '='
            };
        }
        if (col.filter === false || (col.filter !== true && ['time', 'actions', 'number', 'length', 'badge'].includes(col.type))) return col;
        return {
            ...col,
            headerPopup: filterPopup,
            headerPopupIcon: filterIcon,
            headerFilter: () => document.createElement('div'),
            headerFilterFunc: 'like'
        };
    }

    // icon columns fixed to content witdh and rest of table fills the container
    function fitTable(table, el) {
        let natural = null;
        const fit = () => {
            const cols = table.getColumns();

            // Header: title + filter + sort + padding
            const textWidth = node => {
                if (!node) return 0;
                const range = document.createRange();
                range.selectNodeContents(node);
                return range.getBoundingClientRect().width;
            };
            const headerNeed = cols.map(c => Math.ceil(textWidth(c.getElement().querySelector('.tabulator-col-title'))) + 50);

            if (!natural && !cols.some(c => c.getCells().length)) return;
            natural ??= cols.map((c, i) => Math.max(headerNeed[i], ...c.getCells().map(cell => cell.getElement().scrollWidth + 2)));
            el.classList.add('table-fitted');
            const isActions = c => c.getDefinition().type === 'actions';
            const isWrap = c => c.getDefinition().cssClass?.includes('cell-wrap');

            const widths = cols.map((c, i) => isActions(c)
                ? Math.max(natural[i], ...c.getCells().map(cell => cell.getElement().scrollWidth))
                : natural[i]);
            const flex = cols.map((c, i) => i).filter(i => !isActions(cols[i]));
            const avail = el.clientWidth - 2 - cols.reduce((n, c, i) => n + (isActions(c) ? widths[i] : 0), 0);
            const total = flex.reduce((n, i) => n + widths[i], 0);

            if (total > avail) {
                let deficit = total - avail;
                for (const [group, minimum] of [[flex.filter(i => isWrap(cols[i])), 160], [flex, 0], [flex.filter(i => !isWrap(cols[i])), -1], [flex, -1]]) {
                    const floors = group.map(i => minimum < 0 ? 64 : Math.max(minimum, headerNeed[i]));
                    const room = group.reduce((n, i, k) => n + Math.max(0, widths[i] - floors[k]), 0);
                    if (!room) continue;
                    const take = Math.min(deficit, room);
                    group.forEach((i, k) => widths[i] -= take * Math.max(0, widths[i] - floors[k]) / room);
                    deficit -= take;
                    if (deficit <= 0) break;
                }
            } else if (flex.length) {
                const wraps = flex.filter(i => isWrap(cols[i]));
                const weights = flex.map(i => (wraps.length ? wraps.includes(i) : true) ? widths[i] : 0);
                const weightSum = weights.reduce((n, w) => n + w, 0);
                flex.forEach((i, k) => widths[i] += (avail - total) * weights[k] / weightSum);
            }
            let changed = false;
            cols.forEach((c, i) => {
                if (Math.abs(c.getWidth() - widths[i]) < 1) return;
                c.setWidth(Math.floor(widths[i]));
                changed = true;
            });
            if (changed) setTimeout(() => {
                table.getRows().forEach(row => row.normalizeHeight(true));
                table.rowManager.adjustTableSize();
            }, 60);
        };
        table.on('tableBuilt', fit);
        table.on('renderComplete', fit);
        new ResizeObserver(fit).observe(el);
    }

    const trackTables = new Set();

    document.addEventListener('player:change', () => trackTables.forEach(table => table.getRows().forEach(row => row.reformat())));

    function initTables(root = document) {
        root.querySelectorAll('[data-table]:not([data-table-ready])').forEach(el => {
            el.dataset.tableReady = '1';
            const spec = JSON.parse(el.dataset.table);
            loadTabulator(el).then(() => {
                const table = new Tabulator(el, {
                    ...(spec.remote ? {
                        ajaxURL: spec.remote.url,
                        ajaxParams: () => ({...el.tableParams}),
                        paginationMode: 'remote',
                        sortMode: 'remote',
                        filterMode: 'remote',
                        paginationSizeSelector: [25, 50, 100, 200]
                    } : {data: spec.rows}),
                    nestedFieldSeparator: false,    // Column names could contain dots
                    ...(spec.persist ? {
                        persistence: {columns: ['visible']},
                        persistenceMode: false,
                        persistenceID: 'bsn-' + spec.persist,
                        persistenceReaderFunc: (id, type) => {
                            try {
                                return JSON.parse(sessionStorage.getItem(id + '-' + type));
                            } catch (e) {
                                return false;
                            }
                        },
                        persistenceWriterFunc: (id, type, data) => {
                            try {
                                sessionStorage.setItem(id + '-' + type, JSON.stringify(data));
                            } catch (e) {
                                // Storage unavailable
                            }
                        }
                    } : {}),
                    movableColumns: Boolean(spec.movable),
                    selectableRows: spec.play ? 1 : false,
                    ...(spec.height ? {height: spec.height} : {}),
                    columns: spec.columns.map(col => withFilter({
                        ...col,
                        ...TABLE_COLUMN_TYPES[col.type],
                        ...(col.html ? {formatter: htmlFormatter(col.html)} : {}),
                        minWidth: 60,
                        tooltip: !col.html
                    }, spec.rows || [])),

                    layout: spec.layout || 'fitData',
                    columnDefaults: {headerSort: spec.sortable !== false},
                    rowFormatter: row => {
                        const data = row.getData();
                        if (data._class) row.getElement().classList.add(...data._class.split(' '));
                        if (spec.play) row.getElement().classList.toggle('now-playing', Boolean(data.id) && data.id === document.body.dataset.nowPlaying);
                    },
                    pagination: true,
                    paginationSize: spec.pageSize || 25
                });
                el.tabulatorTable = table;
                table.on('tableBuilt', () => el.dispatchEvent(new CustomEvent('table:ready', {bubbles: true, detail: table})));

                // double-click or Enter to play
                if (spec.play) {
                    trackTables.add(table);
                    const play = row => row.getData().id && el.dispatchEvent(new CustomEvent('table:play', {bubbles: true, cancelable: true, detail: {table, data: row.getData()}}));
                    table.on('rowDblClick', (event, row) => play(row));
                    el.tabIndex = 0;
                    el.addEventListener('keydown', event => {
                        const selected = event.key === 'Enter' && table.getSelectedRows()[0];
                        if (selected) play(selected);
                    });
                }
                if (!spec.layout) fitTable(table, el);
                table.on('renderComplete', () => el.dispatchEvent(new CustomEvent('table:render', {bubbles: true})));
            }).catch(() => {
                el.textContent = 'Could not load the table.';
            });
        });
    }

    initTables();

    document.body.addEventListener('htmx:after:swap', () => initTables());

    // htmx requests are same-origin -> attach the CSRF token to every one
    document.body.addEventListener('htmx:config:request', event => {
        const csrfInput = document.querySelector('input[name="csrf_token"]');
        if (csrfInput) event.detail.ctx.request.headers['X-CSRFToken'] = csrfInput.value;
    });

    // htmx's `keyup[key=='Enter']` trigger filters compile the condition with `new Function()`,
    // which our CSP blocks (no 'unsafe-eval') so enter-to-search is defined here
    document.addEventListener('keydown', event => {
        if (event.key !== 'Enter' || event.isComposing) return;
        const input = event.target.closest('#podcastDiscoveryQuery, #radioDiscoveryQuery');
        if (!input) return;
        event.preventDefault();
        const button = input.parentElement.querySelector('button[hx-get]');
        if (button) button.click();
    });

    document.addEventListener('change', event => {
        if (event.target.id === 'podcastOpmlFile' && event.target.files.length) {
            event.target.form.requestSubmit();
        }
    });

    // Let the OPML file be picked again after an import
    document.body.addEventListener('htmx:after:swap', event => {
        const id = event.detail.ctx.target?.id;
        if (id !== 'podcastChannels' && id !== 'accountPodcasts') return;
        const input = document.getElementById('podcastOpmlFile');
        if (input) input.value = '';
    });

    // Runs handler(target, event) after htmx swaps into the element(s) with the given id(s)
    function onSwap(ids, handler) {
        ids = [].concat(ids);
        document.body.addEventListener('htmx:after:swap', event => {
            const target = event.detail.ctx.target;
            if (target && ids.includes(target.id)) handler(target, event);
        });
    }

    // Modal bodies start empty before each swap
    document.body.addEventListener('htmx:before:swap', event => {
        const target = event.detail.ctx.target;
        if (target && target.id.endsWith('ModalBody')) target.replaceChildren();
    });

    // Runs handler(target) after a swap into the element if the new content holds a success flash
    function onSwapSuccess(id, handler) {
        onSwap(id, target => {
            if (target.querySelector('.flash-success')) handler(target);
        });
    }

    // Modal forms swap the list behind them on success -> reset the form and close the modal
    function closeModalsOnSuccess(modals) {
        for (const [listId, pairs] of Object.entries(modals)) {
            onSwapSuccess(listId, () => {
                const store = Alpine.store('modal');
                for (const [modalId, formId] of pairs) {
                    if (store.current !== modalId) continue;
                    const form = formId && document.getElementById(formId);
                    if (form) {
                        form.reset();
                        form.querySelector('.modal-result').innerHTML = '';
                    }
                    store.hide(modalId);
                }
            });
        }
    }

    function usePodcastResult(target) {
        const urlInput = document.getElementById('podcastFeedUrl');
        if (urlInput) urlInput.value = target.dataset.url || '';
        const results = document.getElementById('podcastDiscoveryResults');
        if (results) results.innerHTML = '';
    }

    document.addEventListener('click', event => {
        const target = event.target.closest('[data-action]');
        if (!target) return;

        switch (target.dataset.action) {
            case 'use-podcast-result':
                usePodcastResult(target);
                break;
            case 'toggle-theme':
                toggleTheme();
                break;
            case 'reset-app-password-form':
                document.getElementById('appPasswordForm')?.reset();
                document.getElementById('appPasswordSlot')?.replaceChildren();
                break;
            case 'copy-app-password':
                copyAppPassword(target);
                break;
            case 'pick-file': {
                const fileInput = document.getElementById(target.dataset.target);
                if (fileInput) fileInput.click();
                break;
            }
        }
    });

    window.bsnCommon = {copyText, onSwap, onSwapSuccess, closeModalsOnSuccess, coverArt};

    document.addEventListener('alpine:init', () => {
        // Image picker (radio icons, user avatars)
        Alpine.data('iconPicker', () => ({
            previewSrc: '',
            blobUrl: '',
            dragging: false,

            init() {
                this.previewSrc = this.$el.dataset.src || '';
            },

            revokeBlob() {
                if (this.blobUrl) URL.revokeObjectURL(this.blobUrl);
                this.blobUrl = '';
            },

            showBlob(blob) {
                this.revokeBlob();
                this.blobUrl = URL.createObjectURL(blob);
                this.previewSrc = this.blobUrl;
            },

            pick() {
                this.$refs.file.click();
            },

            onFileChange() {
                const file = this.$refs.file.files[0];
                if (this.$refs.favicon) this.$refs.favicon.value = '';
                if (file) {
                    this.showBlob(file);
                    if (this.$refs.remove) this.$refs.remove.checked = false;
                } else {
                    this.clear();
                }
            },

            onDrop(event) {
                this.dragging = false;
                const accepted = this.$refs.file.accept.split(',').map(a => a.trim().toLowerCase()).filter(Boolean);
                const file = Array.from(event.dataTransfer.files).find(f => accepted.some(a => {
                    if (a.startsWith('.')) return f.name.toLowerCase().endsWith(a);
                    if (a.endsWith('/*')) return f.type.startsWith(a.slice(0, -1));
                    return f.type === a;
                }));
                if (!file) return;
                const dt = new DataTransfer();
                dt.items.add(file);
                this.$refs.file.files = dt.files;
                this.onFileChange();
            },

            clear() {
                this.revokeBlob();
                this.previewSrc = '';
            },

            // Image fetched server-side: it replaces whatever file was chosen
            showFetched(event) {
                this.revokeBlob();
                this.$refs.file.value = '';
                this.previewSrc = event.detail.url;
            },

            // Icon from Radio Browser result
            applyRemote(event) {
                const blob = event.detail.blob;
                this.$refs.file.value = '';
                if (!blob) {
                    this.clear();
                    return;
                }
                this.showBlob(blob);
                if (typeof DataTransfer !== 'undefined') {
                    const dt = new DataTransfer();
                    dt.items.add(new File([blob], 'icon', {type: blob.type || 'application/octet-stream'}));
                    this.$refs.file.files = dt.files;
                }
            }
        }));

        // "Find an image yourself" links
        Alpine.data('artistImageLinks', () => ({
            name: '',
            joint: false,

            init() {
                const input = document.getElementById('artistImageName');
                if (!input) return;
                const preset = this.$el.hasAttribute('data-joint');
                const sync = () => {
                    this.name = input.value;
                    const list = input.list;
                    const option = list && Array.from(list.options).find(o => o.value === input.value);
                    this.joint = option ? option.hasAttribute('data-joint') : preset;
                };
                sync();
                input.addEventListener('input', sync);
            },

            get query() {
                return encodeURIComponent(this.name.trim());
            },

            get hasName() {
                return this.name.trim() !== '';
            },

            get noName() {
                return !this.hasName;
            },

            get lastfmUrl() {
                // Joint credits often have a gazillion user-created duplicates -> so search instead
                return this.joint
                    ? 'https://www.last.fm/search/artists?q=' + this.query
                    : 'https://www.last.fm/music/' + this.query + '/+images';
            },

            get commonsUrl() {
                return 'https://commons.wikimedia.org/w/index.php?search=' + this.query + '&ns6=1&ns14=1';
            }
        }));
    });
})();
