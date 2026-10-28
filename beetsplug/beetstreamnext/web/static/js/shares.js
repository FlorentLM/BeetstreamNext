(function () {
    'use strict';

    bsnCommon.closeModalsOnSuccess({
        sharesPane: [['editShareModal', null]],
    });

    document.addEventListener('click', event => {
        const button = event.target.closest('[data-action="copy-share-url"]');
        if (!button) return;

        bsnCommon.copyText(button.dataset.url, () => {
            const glyph = button.querySelector('.material-symbols-outlined');
            glyph.textContent = 'check';
            setTimeout(() => {
                glyph.textContent = 'content_copy';
            }, 2000);
        });
    });

    // Edit form lazy-loaded
    bsnCommon.onSwap('editShareModalBody', () => Alpine.store('modal').show('editShareModal'));
})();
