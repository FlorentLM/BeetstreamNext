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
    // (delegated: the avatar block is re-rendered by htmx)
    document.addEventListener('change', event => {
        if (event.target.id === 'avatarInput' && event.target.files.length) {
            event.target.form.requestSubmit();
        }
    });

    // Clear the password fields after a successful change
    document.body.addEventListener('htmx:after:swap', event => {
        const target = event.detail.ctx.target;
        if (target?.id !== 'passwordResult') return;
        if (target.querySelector('.flash-success')) document.getElementById('passwordForm')?.reset();
    });

    document.addEventListener('click', event => {
        const target = event.target.closest('[data-action="use-podcast-result"]');
        if (!target) return;

        const urlInput = document.getElementById('podcastFeedUrl');
        if (urlInput) urlInput.value = target.dataset.url || '';
        const results = document.getElementById('podcastDiscoveryResults');
        if (results) results.innerHTML = '';
    });
})();
