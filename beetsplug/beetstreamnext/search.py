import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.albums import get_song_counts
from beetsplug.beetstreamnext.utils import (
    subsonic_error, subsonic_response,
    remove_accents,
    map_artist, map_album, map_song
)


@app.route('/rest/search', methods=["GET", "POST"])
@app.route('/rest/search.view', methods=["GET", "POST"])

@app.route('/rest/search2', methods=["GET", "POST"])
@app.route('/rest/search2.view', methods=["GET", "POST"])

@app.route('/rest/search3', methods=["GET", "POST"])
@app.route('/rest/search3.view', methods=["GET", "POST"])
def search(ver=None):
    r = flask.request.values
    query_str = r.get('query') or ''

    song_count = int(r.get('songCount', 20))
    song_offset = int(r.get('songOffset', 0))
    album_count = int(r.get('albumCount', 20))
    album_offset = int(r.get('albumOffset', 0))
    artist_count = int(r.get('artistCount', 20))
    artist_offset = int(r.get('artistOffset', 0))

    if 'search2' in flask.request.path:
        tag = 'searchResult2'
    elif 'search3' in flask.request.path:
        tag = 'searchResult3'
    else:
        tag = 'searchResult'

    if query_str.startswith('"') and query_str.endswith('"'):
        query_str = query_str[1:-1]

    if query_str.startswith('b:') or query_str.startswith('beets:'):
        clean_query = query_str.split(':', 1)[1].strip()

        try:
            beets_albums = list(flask.g.lib.albums(clean_query))
            beets_songs = list(flask.g.lib.items(clean_query))

            artist_names = set()
            for a in beets_albums:
                if a.albumartist: artist_names.add(a.albumartist)
            for s in beets_songs:
                if s.artist: artist_names.add(s.artist)

            artists = sorted(list(artist_names))

            songs = beets_songs[song_offset: song_offset + song_count]
            albums = beets_albums[album_offset: album_offset + album_count]
            artists = artists[artist_offset: artist_offset + artist_count]

        except Exception:
            return subsonic_error(70, r.get('f', 'xml'))

    else:
        if not query_str:
            if ver == 2 or tag == 'searchResult2':
                # search2 does not support empty queries: return an empty response
                return subsonic_error(10, r.get('f', 'xml'))

            # search3 "must support an empty query and return all the data"
            # https://opensubsonic.netlify.app/docs/endpoints/search3/
            pattern = "%"
        else:
            pattern = f"%{query_str.lower()}%"

        with flask.g.lib.transaction() as tx:

            songs = list(tx.query(
                """
                SELECT * FROM items 
                WHERE lower(title) LIKE ? 
                ORDER BY title 
                LIMIT ? OFFSET ?
                """, (pattern, song_count, song_offset)
            ))

            albums = list(tx.query(
                """
                SELECT * FROM albums 
                WHERE lower(album) LIKE ? 
                ORDER BY album 
                LIMIT ? OFFSET ?
                """, (pattern, album_count, album_offset)
            ))

            artists = [row[0] for row in tx.query(
                """
                SELECT DISTINCT albumartist
                FROM albums
                WHERE lower(albumartist) LIKE ? AND albumartist IS NOT NULL
                LIMIT ? OFFSET ?
                """, (pattern, artist_count, artist_offset)
            )]

        # TODO - do the sort in the SQL query instead?
        artists.sort(key=lambda name: remove_accents(name).upper())

    song_counts = get_song_counts(albums)

    payload = {
        tag: {
            'artist': [map_artist(name, with_albums=False) for name in artists],
            'album': [map_album(alb, with_songs=False, song_counts=song_counts) for alb in albums],
            'song': [map_song(s) for s in songs]
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))
