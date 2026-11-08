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

    let searchTimer = null;
    search.addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
            if (search.value.trim()) filters.q = search.value.trim(); else delete filters.q;
            const table = el.tabulatorTable;
            table.getPage() === 1 ? table.replaceData() : table.setPage(1);
        }, 250);
    });
})();
