(function () {
    'use strict';

    const root = document.getElementById('library');
    if (!root) return;

    const el = root.querySelector('[data-table]');
    const search = document.getElementById('libSearch');

    const filters = el.tableParams = {};

    // Columns visibility menu
    const PRESETS = {
        'all': () => true,
        'non-empty': column => column.getDefinition().populated,
        'default': column => column.getDefinition().default
    };

    const columnsButton = document.getElementById('libColumns');
    columnsButton.addEventListener('click', () => {
        const table = el.tabulatorTable;
        const columns = table.getColumns();
        const {right, bottom} = columnsButton.getBoundingClientRect();
        const syncs = [];

        // All presets share one menu row of icon buttons
        const presets = document.createElement('span');
        presets.className = 'lib-menu-label lib-menu-preset';
        presets.append(document.getElementById('libPresets').content.cloneNode(true));
        presets.querySelectorAll('button').forEach(button => button.addEventListener('click', event => {
            event.stopPropagation();    // Keep menu open
            const shown = PRESETS[button.dataset.preset];
            columns.forEach(column => shown(column) ? column.show() : column.hide());
            syncs.forEach(sync => sync());
        }));

        const items = columns.map(column => {
            const icon = document.createElement('span');
            icon.className = 'material-symbols-outlined';
            const sync = () => icon.textContent = column.isVisible() ? 'check' : '';
            sync();
            syncs.push(sync);

            const label = document.createElement('span');
            label.className = 'lib-menu-label';
            label.append(column.getDefinition().title, icon);
            return {
                label,
                action: event => {
                    event.stopPropagation();
                    column.toggle();
                    sync();
                }
            };
        });
        table.modules.menu.loadMenu(new MouseEvent('click', {
            clientX: right,
            clientY: bottom
        }), {getComponent: () => table}, [{
            label: presets,
            action: event => event.stopPropagation()
        }, {separator: true}, ...items]);
    });

    // Search terms and column filters as dismissable badges
    const filterBadges = document.getElementById('libFilters');
    const terms = [];

    function reload() {
        const q = [...terms, search.value.trim()].filter(Boolean).join(' ');
        if (q) filters.q = q; else delete filters.q;
        const table = el.tabulatorTable;
        table.getPage() === 1 ? table.replaceData() : table.setPage(1);
    }

    function renderBadges() {
        const table = el.tabulatorTable;
        const active = table.getHeaderFilters().filter(filter => filter.value !== '' && filter.value != null);
        table.getColumns().forEach(column => column.getElement().querySelector('.table-filter-icon')
            ?.classList.toggle('active', active.some(filter => filter.field === column.getField())));

        const badge = (text, remove) => {
            const node = document.createElement('span');
            node.className = 'badge lib-filter-badge';
            const label = document.createElement('span');
            label.className = 'lib-filter-label';
            label.textContent = label.title = text;
            const dismiss = document.createElement('button');
            dismiss.type = 'button';
            dismiss.className = 'lib-filter-dismiss';
            dismiss.setAttribute('aria-label', `Remove filter ${text}`);
            dismiss.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">close</span>';
            dismiss.addEventListener('click', remove);
            node.append(label, dismiss);
            return node;
        };
        filterBadges.replaceChildren(
            ...terms.map(term => badge(`Search: ${term}`, () => {
                terms.splice(terms.indexOf(term), 1);
                renderBadges();
                reload();
            })),
            ...active.map(({field, value}) => badge(`${table.getColumn(field).getDefinition().title}: ${value}`, () => table.getColumn(field).setHeaderFilterValue('')))
        );
    }

    el.addEventListener('table:render', renderBadges);

    let searchTimer = null;
    search.addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(reload, 250);
    });
    search.addEventListener('keydown', event => {
        const term = search.value.trim();
        if (event.key !== 'Enter' || event.isComposing || !term) return;
        event.preventDefault();
        clearTimeout(searchTimer);
        if (!terms.includes(term)) terms.push(term);
        search.value = '';
        renderBadges();
        reload();
    });
})();
