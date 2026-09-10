(function () {
    'use strict';

    const DISPLAY_MS = 5000;
    const FADE_MS = 300;
    const COLLAPSE_MS = 250;

    document.querySelectorAll('.flash').forEach(el => {
        setTimeout(() => {
            el.style.transition = `opacity ${FADE_MS}ms ease`;
            el.style.opacity = '0';

            setTimeout(() => {
                el.style.overflow = 'hidden';
                el.style.height = el.offsetHeight + 'px';
                el.offsetHeight;   // force reflow so height transition animates

                el.style.transition = `height ${COLLAPSE_MS}ms ease, margin ${COLLAPSE_MS}ms ease, padding ${COLLAPSE_MS}ms ease, border-width ${COLLAPSE_MS}ms ease`;
                el.style.height = '0px';
                el.style.marginTop = '0';
                el.style.marginBottom = '0';
                el.style.paddingTop = '0';
                el.style.paddingBottom = '0';
                el.style.borderWidth = '0';

                setTimeout(() => el.remove(), COLLAPSE_MS);
            }, FADE_MS);
        }, DISPLAY_MS);
    });
})();
