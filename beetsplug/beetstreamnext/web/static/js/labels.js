(function () {
    'use strict';

    const CONTROL = 'input:not([type="hidden"]):not([type="file"]):not([type="checkbox"]):not([type="radio"]), select, textarea';
    let counter = 0;

    function findControl(label) {
        for (let el = label.nextElementSibling; el; el = el.nextElementSibling) {
            if (el.tagName === 'LABEL') return null;
            const control = el.matches(CONTROL) ? el : el.querySelector(CONTROL);
            if (control) return control;
        }
        return null;
    }

    function fix(label) {
        if (label.htmlFor || label.querySelector('input, select, textarea')) return;
        const control = findControl(label);
        if (!control || control.hasAttribute('aria-label') || control.hasAttribute('aria-labelledby')) return;
        if (!control.id) control.id = 'auto-field-' + (++counter);
        label.htmlFor = control.id;
    }

    function scan(root) {
        if (root.nodeType !== 1) return;
        if (root.matches('label')) fix(root);
        root.querySelectorAll('label').forEach(fix);
    }

    function init() {
        scan(document.body);
        new MutationObserver(muts => muts.forEach(m => m.addedNodes.forEach(scan)))
            .observe(document.body, {childList: true, subtree: true});
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
