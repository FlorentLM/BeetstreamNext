(function () {
    'use strict';

    // Clear feed URL box after successful subscribe
    bsnCommon.onSwapSuccess('accountPodcasts', () => document.getElementById('addPodcastForm')?.reset());

    // Avatar picking
    // (delegated: the avatar block is re-rendered by htmx)
    document.addEventListener('change', event => {
        if (event.target.id === 'avatarInput' && event.target.files.length) {
            event.target.form.requestSubmit();
        }
    });

    // Clear the password fields after a successful change
    bsnCommon.onSwapSuccess('passwordResult', () => document.getElementById('passwordForm')?.reset());
})();
