(function () {
    'use strict';


    const COLLAPSE_MS = 250;

    function dismiss(el) {
        const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (reduce) {
            el.remove();
            return;
        }
        el.style.overflow = 'hidden';
        el.style.height = el.offsetHeight + 'px';
        el.style.transition = `opacity ${COLLAPSE_MS}ms ease, height ${COLLAPSE_MS}ms ease, margin ${COLLAPSE_MS}ms ease, padding ${COLLAPSE_MS}ms ease, border-width ${COLLAPSE_MS}ms ease`;
        el.offsetHeight;   // force reflow so the transition animates
        el.style.opacity = '0';
        el.style.height = '0px';
        el.style.marginTop = '0';
        el.style.marginBottom = '0';
        el.style.paddingTop = '0';
        el.style.paddingBottom = '0';
        el.style.borderWidth = '0';
        setTimeout(() => el.remove(), COLLAPSE_MS);
    }

    function enhance(el) {
        if (el.dataset.flashReady) return;
        el.dataset.flashReady = '1';

        if (!el.hasAttribute('role')) {
            el.setAttribute('role', el.classList.contains('flash-error') ? 'alert' : 'status');
        }

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'flash-dismiss';
        btn.setAttribute('aria-label', 'Dismiss message');
        btn.innerHTML = '<span aria-hidden="true">&times;</span>';
        btn.addEventListener('click', () => dismiss(el));
        el.appendChild(btn);
    }

    function scan() {
        document.querySelectorAll('.flash').forEach(enhance);
    }

    scan();

    // Messages rendered by htmx partials
    // (scans the whole document: with outerHTML swaps the event's target is the old, detached element)
    document.body.addEventListener('htmx:after:swap', scan);
})();
