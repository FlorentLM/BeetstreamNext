(function () {
    'use strict';

    function enhance(input) {
        if (input.dataset.pwToggle) return;
        input.dataset.pwToggle = '1';

        const wrap = document.createElement('span');
        wrap.className = 'password-toggle-wrap';
        input.parentNode.insertBefore(wrap, input);
        wrap.appendChild(input);

        wrap.addEventListener('mousedown', e => {
            if (e.target === wrap) {
                e.preventDefault();
                input.focus();
            }
        });

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'password-toggle-btn';
        btn.setAttribute('aria-label', 'Show password');
        btn.setAttribute('aria-pressed', 'false');
        btn.title = 'Show password';
        btn.innerHTML = '<span class="material-symbols-outlined" aria-hidden="true">visibility_off</span>';
        wrap.appendChild(btn);

        const icon = btn.firstChild;

        function reset() {
            input.type = 'password';
            icon.textContent = 'visibility_off';
            btn.setAttribute('aria-label', 'Show password');
            btn.setAttribute('aria-pressed', 'false');
            btn.title = 'Show password';
        }

        function sync() {
            const typed = input.value !== '';
            wrap.classList.toggle('has-value', typed);
            if (!typed) reset();
        }

        // User input reveals the button
        input.addEventListener('input', sync);
        if (input.form) input.form.addEventListener('reset', () => setTimeout(() => {
            wrap.classList.remove('has-value');
            reset();
        }));

        btn.addEventListener('click', () => {
            const show = input.type === 'password';
            input.type = show ? 'text' : 'password';
            icon.textContent = show ? 'visibility' : 'visibility_off';
            const label = show ? 'Hide password' : 'Show password';
            btn.setAttribute('aria-label', label);
            btn.setAttribute('aria-pressed', show ? 'true' : 'false');
            btn.title = label;
            input.focus();
        });
    }

    function scan(root) {
        if (root.nodeType !== 1) return;
        if (root.matches('input[type="password"]')) enhance(root);
        root.querySelectorAll('input[type="password"]').forEach(enhance);
    }

    function init() {
        scan(document.body);
        new MutationObserver(muts => {
            muts.forEach(m => m.addedNodes.forEach(scan));
        }).observe(document.body, {childList: true, subtree: true});
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
