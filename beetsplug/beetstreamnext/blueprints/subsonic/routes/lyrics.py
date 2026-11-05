from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
import flask

from .. import subsonic_bp

from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.utils.general import api_bool
from beetsplug.beetstreamnext.utils.lrc import Lrc
from beetsplug.beetstreamnext.blueprints.subsonic.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.library.resolve import Resolve, beets_abspath

from beetsplug.beetstreamnext.application import app

if TYPE_CHECKING:
    from beetsplug.lyrics import LyricsPlugin
    from beets.plugins import BeetsPlugin


def _get_lyrics_plugin() -> BeetsPlugin | LyricsPlugin | None:
    """
    Find the beets lyrics plugin instance. Should work with any version of Beets.
    """
    from beets import plugins
    for p in plugins.find_plugins():
        if p.name == 'lyrics':
            return p
    try:
        from beetsplug.lyrics import LyricsPlugin
        return LyricsPlugin()
    except (ImportError, Exception) as e:
        bsn_logger.error(f'Could not load beets lyrics plugin: {e}')
        return None


@lru_cache(maxsize=512)
def _load_lrc(path: str, mtime_ns: int) -> str | None:
    """
    Decodes an LRC file.
    (mtime is part of the cache key so modified files are re-read)
    """
    raw = Path(path).read_bytes()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = raw.decode('latin-1')
    return text.strip() or None


def _read_sidecar(item) -> str | None:
    """Reads the `.lrc` file next to the audio file (if any)."""
    try:
        audio = beets_abspath(item)
        exact = audio.with_suffix('.lrc')
        if exact.is_file():
            lrc = exact
        else:
            wanted = exact.name.casefold()
            lrc = next((p for p in audio.parent.iterdir() if p.name.casefold() == wanted and p.is_file()), None)
        if lrc is None:
            return None
        return _load_lrc(str(lrc), lrc.stat().st_mtime_ns)
    except OSError as e:
        bsn_logger.debug(f'Could not read sidecar lyrics: {e}')
        return None


def _fetch_lyrics_data(item) -> dict | None:

    if not item:
        return None

    sidecar_text = _read_sidecar(item)
    db_text = str(item.get('lyrics') or '').strip() or None

    candidates = [db_text, sidecar_text]

    # Synced lyrics preferred to plain
    sets = []
    for text in sorted((t for t in candidates if t), key=lambda t: not Lrc(t).synced):
        plain = Lrc(text).plain()
        if all(plain != Lrc(s).plain() for s in sets):  # Same words = same version, keep the better one
            sets.append(text)

    lyrics_text = sets[0] if sets else None

    if not lyrics_text:
        if not app.config.get('fetch_lyrics'):
            bsn_logger.info('No lyrics in database, and `fetch_lyrics` not enabled in config. Skipping.')
        else:
            lyrics_plugin = _get_lyrics_plugin()
            if lyrics_plugin:
                try:
                    if app.config.get('save_lyrics'):
                        lyrics_plugin.add_item_lyrics(item, write=False)  # write is 'write to song file' so NOPE
                        lyrics_text = item.get('lyrics')
                    else:
                        lyrics = lyrics_plugin.find_lyrics(item)
                        lyrics_text = lyrics.text
                except Exception as e:
                    bsn_logger.error(f'Error calling lyrics plugin: {e}')
            else:
                bsn_logger.info('Lyrics plugin not found in beets. Is it enabled?')

    if not lyrics_text:
        return None

    if not sets:
        sets = [str(lyrics_text)]

    return {
        'text': str(lyrics_text),
        'sets': sets,
        'lang': item.get('lyrics_language') or 'xxx',   # OpenSubsonic's fallback for unknown language is xxx
        'instrumental': bool(item.get('lyrics_instrumental')),
        'artist': item.get('artist') or '',
        'title': item.get('title') or ''
    }


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getLyrics/
@subsonic_bp.route('/getLyrics', methods=['GET', 'POST'])
@subsonic_bp.route('/getLyrics.view', methods=['GET', 'POST'])
def endpoint_get_lyrics() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    artist = r.get('artist', default='', type=safe_str)
    title = r.get('title', default='', type=safe_str)

    if not artist or not title:
        return subsonic_error(10, resp_fmt=resp_fmt)

    with flask.g.lib.transaction() as tx:
        rows = tx.query(
            """
            SELECT id FROM items 
            WHERE lower(artist) = lower(?) AND lower(title) = lower(?) 
            LIMIT 1
            """, (artist, title)
        )

    if not rows:
        return subsonic_error(70, message='Song not found.', resp_fmt=resp_fmt)

    item = flask.g.lib.get_item(rows[0][0])
    data = _fetch_lyrics_data(item)

    if not data:
        return subsonic_error(70, message='Lyrics not found.', resp_fmt=resp_fmt)

    payload = {
        'lyrics': {
            'artist': data['artist'],
            'title': data['title'],
            'value': Lrc(data['text']).plain()
        }
    }
    return subsonic_response(payload, resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getLyricsBySongId/
@subsonic_bp.route('/getLyricsBySongId', methods=['GET', 'POST'])
@subsonic_bp.route('/getLyricsBySongId.view', methods=['GET', 'POST'])
def endpoint_get_lyrics_by_song_id() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)      # Required
    enhanced = r.get('enhanced', default=False, type=api_bool)

    if not req_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    item = Resolve.song(req_id)

    if not item:
        return subsonic_error(70, message='Song not found.', resp_fmt=resp_fmt)

    data = _fetch_lyrics_data(item)
    if not data:
        return subsonic_error(70, message='Lyrics not found.', resp_fmt=resp_fmt)

    structured_list = []
    for text in data['sets']:
        lrc = Lrc(text)
        structured = {
            'displayArtist': data['artist'],
            'displayTitle': data['title'],
            'lang': data['lang'],
            'synced': lrc.synced,
            'line': lrc.structured(cues=enhanced)
        }
        if enhanced:
            structured['kind'] = 'main'
        structured_list.append(structured)

    payload = {
        'lyricsList': {
            'structuredLyrics': structured_list
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)