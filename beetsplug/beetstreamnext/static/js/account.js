(function () {
    'use strict';

    // Clear feed URL box after successful subscribe
    document.body.addEventListener('htmx:after:swap', event => {
        if (event.detail.ctx.target?.id !== 'accountPodcasts') return;
        if (event.detail.ctx.target.querySelector('.flash-success')) {
            const form = document.getElementById('addPodcastForm');
            if (form) form.reset();
        }
    });

    // Avatar picking
    const avatarInput = document.getElementById('avatarInput');
    if (avatarInput) {
        avatarInput.addEventListener('change', () => {
            if (avatarInput.files.length) avatarInput.form.submit();
        });
    }

    document.addEventListener('click', event => {
        const target = event.target.closest('[data-action="use-podcast-result"]');
        if (!target) return;

        const urlInput = document.getElementById('podcastFeedUrl');
        if (urlInput) urlInput.value = target.dataset.url || '';
        const results = document.getElementById('podcastDiscoveryResults');
        if (results) results.innerHTML = '';
    });

    // Confirm dialogs
    const confirmModal = document.getElementById('confirmModal');
    const confirmText = document.getElementById('confirmText');
    let confirmResolve = null;

    function askConfirm(message) {
        return new Promise(resolve => {
            confirmResolve = resolve;
            confirmText.textContent = message;
            confirmModal.classList.add('active');
        });
    }

    function settleConfirm(result) {
        confirmModal.classList.remove('active');
        if (!confirmResolve) return;
        const resolve = confirmResolve;
        confirmResolve = null;
        resolve(result);
    }

    document.getElementById('confirmOk').addEventListener('click', () => settleConfirm(true));
    document.getElementById('confirmCancel').addEventListener('click', () => settleConfirm(false));
    confirmModal.addEventListener('click', event => {
        if (event.target === confirmModal) settleConfirm(false);
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') settleConfirm(false);
    });

    document.body.addEventListener('htmx:confirm', event => {
        event.preventDefault();
        askConfirm(event.detail.ctx.confirm).then(ok => {
            if (ok) event.detail.issueRequest();
            else event.detail.dropRequest();
        });
    });

    document.addEventListener('submit', event => {
        const form = event.target.closest('form[data-confirm]');
        if (!form || form.dataset.confirmed) return;
        event.preventDefault();
        askConfirm(form.dataset.confirm).then(ok => {
            if (!ok) return;
            form.dataset.confirmed = '1';
            form.submit();
        });
    });
})();
