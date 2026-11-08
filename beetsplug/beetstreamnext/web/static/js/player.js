(function () {
    'use strict';

    const player = document.getElementById('player');
    const audio = document.getElementById('audio');
    if (!player || !audio) return;

    const {coverArt} = bsnCommon;
    const $ = id => document.getElementById(id);
    const fmt = secs => Math.floor(secs / 60) + ':' + String(Math.floor(secs % 60)).padStart(2, '0');

    let queue = [];
    let index = -1;

    function playAt(i) {
        const track = queue[i];
        if (!track) return;
        index = i;
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
    }

    function step(direction) {
        if (queue[index + direction]) playAt(index + direction);
    }

    window.bsnPlayer = {
        play(tracks, start) {
            queue = tracks;
            playAt(start);
        },
        current: () => queue[index]?.id ?? null,
        pick: null
    };

    document.addEventListener('table:play', event => {
        if (event.defaultPrevented) return;
        const tracks = event.detail.table.getData('active').filter(row => row.id);
        window.bsnPlayer.play(tracks, tracks.findIndex(row => row.id === event.detail.data.id));
    });

    $('pPlay').addEventListener('click', () => {
        if (index >= 0 && !audio.paused) return audio.pause();
        if (window.bsnPlayer.pick?.()) return;
        if (index >= 0) audio.play();
    });
    $('pPrev').addEventListener('click', () => audio.currentTime > 3 ? (audio.currentTime = 0) : step(-1));
    $('pNext').addEventListener('click', () => step(1));
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
        $('pDur').textContent = fmt(audio.duration || queue[index]?.secs || 0);
        if (audio.duration) $('pSeek').value = audio.currentTime / audio.duration * 1000;
    });
    audio.addEventListener('ended', () => step(1));
    audio.addEventListener('error', () => {
        if (audio.getAttribute('src')) $('pSub').textContent = 'This track could not be played.';
    });
})();
