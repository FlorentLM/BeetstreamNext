(function () {
    'use strict';

    const player = document.getElementById('player');
    const audio = document.getElementById('audio');
    if (!player || !audio) return;

    const {coverArt, userprefsUrl, fetchUserprefs, setUserpref} = bsnCommon;
    const $ = id => document.getElementById(id);
    const fmt = secs => Math.floor(secs / 60) + ':' + String(Math.floor(secs % 60)).padStart(2, '0');

    const pageTitle = document.title;
    let track = null;
    let table = null;

    function playTrack(next, source) {
        if (!next) return;
        track = next;
        table = source;
        audio.src = player.dataset.stream.slice(0, -1) + encodeURIComponent(track.id);
        audio.play().catch(() => {
        });

        player.hidden = false;
        $('pTitle').textContent = track.title;
        $('pSub').textContent = [track.artist, track.album].filter(Boolean).join(' · ');
        $('pArt').replaceChildren(coverArt(track, player.dataset.art, 128));
        document.title = track.title + ' - BeetstreamNext';

        document.body.dataset.nowPlaying = track.id;
        document.dispatchEvent(new CustomEvent('player:change'));
        loadUserprefs();
    }

    // Heart and thumbs down icons
    const likeButton = $('pLike');
    const dislikeButton = $('pDislike');

    function renderUserprefs(state) {
        if (!likeButton) return;
        likeButton.hidden = dislikeButton.hidden = !state;
        if (!state) return;
        likeButton.classList.toggle('active', state.starred);
        likeButton.setAttribute('aria-pressed', String(state.starred));
        likeButton.setAttribute('aria-label', state.starred ? 'Remove from favorites' : 'Add to favorites');
        dislikeButton.classList.toggle('active', state.disliked);
        dislikeButton.setAttribute('aria-pressed', String(state.disliked));
        dislikeButton.setAttribute('aria-label', state.disliked ? 'Remove dislike' : 'Dislike');
    }

    function loadUserprefs() {
        renderUserprefs(null);
        const asked = track;
        if (!likeButton || !userprefsUrl(asked.id)) return;
        fetchUserprefs(asked.id).then(state => track === asked && renderUserprefs(state), () => {});
    }

    function toggleUserpref(button, on, off) {
        const asked = track;
        if (!asked) return;
        setUserpref(asked.id, button.classList.contains('active') ? off : on).then(state => track === asked && renderUserprefs(state), () => {});
    }

    likeButton?.addEventListener('click', () => toggleUserpref(likeButton, 'star', 'unstar'));
    dislikeButton?.addEventListener('click', () => toggleUserpref(dislikeButton, 'dislike', 'undislike'));

    document.addEventListener('userprefs:change', event => {
        if (track && event.detail.id === track.id) renderUserprefs(event.detail.state);
    });

    function step(direction) {
        if (!table) return;     // Single track no queue
        const rows = table.getData('active').filter(row => row.id);
        const at = rows.findIndex(row => row.id === track.id);
        playTrack(at < 0 ? rows[0] : rows[at + direction], table);
    }

    document.addEventListener('click', event => {
        const button = event.target.closest('[data-play]');
        if (button) playTrack(JSON.parse(button.dataset.play), null);
    });
    document.addEventListener('table:play', event => playTrack(event.detail.data, event.detail.table));

    $('pPlay').addEventListener('click', () => {
        if (!track) return;
        audio.paused ? audio.play() : audio.pause();
    });
    $('pClose').addEventListener('click', () => {
        audio.pause();
        audio.removeAttribute('src');
        audio.load();
        track = null;
        table = null;
        renderUserprefs(null);
        player.hidden = true;
        delete document.body.dataset.nowPlaying;
        document.title = pageTitle;
        document.dispatchEvent(new CustomEvent('player:change'));
    });
    $('pPrev').addEventListener('click', () => !track || audio.currentTime > 3 ? (audio.currentTime = 0) : step(-1));
    $('pNext').addEventListener('click', () => track && step(1));
    $('pSeek').addEventListener('input', event => {
        if (audio.duration) audio.currentTime = event.target.value / 1000 * audio.duration;
    });

    $('pVol').addEventListener('input', event => {
        const percent = Number(event.target.value);
        audio.volume = percent / 100;
        $('pVolIcon').textContent = percent === 0 ? 'volume_off' : 'volume_up';
        try {
            localStorage.setItem('bsn-player-volume', String(percent));
        } catch (e) {
            // Storage unavailable
        }
    });
    try {
        const saved = localStorage.getItem('bsn-player-volume');
        if (saved !== null) $('pVol').value = saved;
    } catch (e) {
        // ignore
    }
    audio.volume = $('pVol').value / 100;

    const syncPlayButton = () => {
        $('pPlayIcon').textContent = audio.paused ? 'play_arrow' : 'pause';
        $('pPlay').setAttribute('aria-label', audio.paused ? 'Play' : 'Pause');
    };
    audio.addEventListener('play', syncPlayButton);
    audio.addEventListener('pause', syncPlayButton);
    audio.addEventListener('timeupdate', () => {
        $('pCur').textContent = fmt(audio.currentTime);
        $('pDur').textContent = fmt(audio.duration || track?.secs || 0);
        if (audio.duration) $('pSeek').value = audio.currentTime / audio.duration * 1000;
    });
    audio.addEventListener('ended', () => track && step(1));
    audio.addEventListener('error', () => {
        if (audio.getAttribute('src')) $('pSub').textContent = 'This track could not be played.';
    });
})();
