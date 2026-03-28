import flask

from beets.plugins import find_plugins
from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.utils import subsonic_response, subsonic_error, sub_to_beets_song


def _fetch_lyrics(item):

    if not item:
        return None

    if item.get('lyrics'):
        return item.lyrics

    lyrics_plugin = next((p for p in find_plugins() if p.name == 'lyrics'), None)
    if lyrics_plugin:
        try:
            lyrics_plugin.add_item_lyrics(item, False)
            if item.lyrics:
                return item.lyrics
        except Exception as e:
            app.logger.error(f"Error fetching lyrics via beets plugin: {e}")

    return None


@app.route('/rest/getLyrics', methods=["GET", "POST"])
@app.route('/rest/getLyrics.view', methods=["GET", "POST"])
def endpoint_get_lyrics():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)
    artist = r.get('artist', default='', type=str)
    title = r.get('title', default='', type=str)

    if not artist or not title:
        return subsonic_error(10, resp_fmt=resp_fmt)

    with flask.g.lib.transaction() as tx:
        rows = tx.query(
            """
            SELECT id 
            FROM items 
            WHERE lower(albumartist) = lower(?) AND lower(title) = lower(?) LIMIT 1
            """, (artist, title)
        )

    if not rows:
        return subsonic_error(70, message="Song not found", resp_fmt=resp_fmt)

    item = flask.g.lib.get_item(rows[0][0])
    lyrics_text = _fetch_lyrics(item)

    payload = {
        'lyrics': {
            'artist': artist,
            'title': title,
            'value': lyrics_text or ''
        }
    }
    return subsonic_response(payload, resp_fmt)


@app.route('/rest/getLyricsBySongId', methods=["GET", "POST"])
@app.route('/rest/getLyricsBySongId.view', methods=["GET", "POST"])
def endpoint_get_lyrics_by_song_id():

    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)
    req_id = r.get('id', default='', type=str)

    if not req_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    beets_id = sub_to_beets_song(req_id)
    item = flask.g.lib.get_item(beets_id)

    if not item:
        return subsonic_error(70, message="Song not found", resp_fmt=resp_fmt)

    lyrics_text = _fetch_lyrics(item)

    lines = [{'value': line} for line in lyrics_text.split('\n')] if lyrics_text else []
    payload = {
        'lyricsList': {
            'structuredLyrics': [
                {
                    'displayArtist': item.get('artist') or '',
                    'displayTitle': item.get('title') or '',
                    'lang': item.get('language') or 'xxx',  # OpenSubsonic's fallback for unknown language is xxx
                    'synced': False,
                    'line': lines
                }
            ]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)