import concurrent.futures
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.albums import get_song_counts
from beetsplug.beetstreamnext.utils import (
    subsonic_error, subsonic_response,
    remove_accents,
    map_artist, map_album, map_song
)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/search/
@app.route('/rest/search', methods=["GET", "POST"])
@app.route('/rest/search.view', methods=["GET", "POST"])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/search2/
@app.route('/rest/search2', methods=["GET", "POST"])
@app.route('/rest/search2.view', methods=["GET", "POST"])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/search3/
@app.route('/rest/search3', methods=["GET", "POST"])
@app.route('/rest/search3.view', methods=["GET", "POST"])
def endpoint_search():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=str)
    song_count = r.get('songCount', default=20, type=int)
    song_offset = r.get('songOffset', default=0, type=int)
    album_count = r.get('albumCount', default=20, type=int)
    album_offset = r.get('albumOffset', default=0, type=int)
    artist_count = r.get('artistCount', default=20, type=int)
    artist_offset = r.get('artistOffset', default=0, type=int)

    query_untrunc = r.get('query', default='', type=str)
    if query_untrunc.startswith('"') and query_untrunc.endswith('"'):
        query_untrunc = query_untrunc[1:-1]
    query = query_untrunc[:256]

    if 'search2' in flask.request.path:
        tag = 'searchResult2'
    elif 'search3' in flask.request.path:
        tag = 'searchResult3'
    else:
        tag = 'searchResult'

    artist_prefetch = {}

    # Beets query
    if query.startswith(('b:', 'beets:')):
        clean_query = query.split(':', 1)[1].strip()

        try:
            beets_albums = list(flask.g.lib.albums(clean_query))
            beets_songs = list(flask.g.lib.items(clean_query))

            # Dedup artists (from albums and songs)
            a_artists = {a.albumartist for a in beets_albums if a.albumartist}
            s_artists = {s.artist for s in beets_songs if s.artist}
            artist_names = a_artists.union(s_artists)

            sorted_artists = sorted(list(artist_names), key=lambda x: remove_accents(x).lower())

            songs = beets_songs[song_offset: song_offset + song_count]
            albums = beets_albums[album_offset: album_offset + album_count]
            artists = sorted_artists[artist_offset: artist_offset + artist_count]

        except Exception:
            return subsonic_error(70, resp_fmt=resp_fmt)

    # Normal SQL search
    else:
        if not query:
            if tag == 'searchResult2':
                # search2 does not support empty queries: return an empty response
                return subsonic_error(10, resp_fmt=resp_fmt)
            # search3 "must support an empty query and return all the data"
            # https://opensubsonic.netlify.app/docs/endpoints/search3/
            pattern = "%"
        else:
            pattern = f"%{query.lower()}%"

        lib = app.config.get('lib')

        def search_songs():
            with lib.transaction() as tx:
                return list(tx.query(
                    """
                    SELECT * FROM items 
                    WHERE lower(title) LIKE ? 
                    ORDER BY title COLLATE NOCASE 
                    LIMIT ? OFFSET ?
                    """, (pattern, song_count, song_offset)
                ))

        def search_albums():
            with lib.transaction() as tx:
                return list(tx.query(
                    """
                    SELECT * FROM albums 
                    WHERE lower(album) LIKE ? 
                    ORDER BY album COLLATE NOCASE 
                    LIMIT ? OFFSET ?
                    """, (pattern, album_count, album_offset)
                ))

        def search_artists():
            with lib.transaction() as tx:
                return list(tx.query(
                    """
                    SELECT albumartist, COUNT(*), mb_albumartistid
                    FROM albums
                    WHERE lower(albumartist) LIKE ?
                      AND albumartist IS NOT NULL
                    GROUP BY albumartist
                    ORDER BY albumartist COLLATE NOCASE
                    LIMIT ? OFFSET ?
                    """, (pattern, artist_count, artist_offset)
                ))

        # TODO: I am not sure this is worth it actually. Should bench it
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            future_songs = executor.submit(search_songs)
            future_albums = executor.submit(search_albums)
            future_artists = executor.submit(search_artists)

            songs = future_songs.result()
            albums = future_albums.result()
            artist_rows = future_artists.result()

        artists = []
        for row in artist_rows:
            name, count, mbid = row[0], row[1], row[2]
            artists.append(name)
            artist_prefetch[name] = {'album_count': count, 'mbid': mbid}

    song_counts = get_song_counts(albums)

    payload = {
        tag: {
            'artist': [map_artist(name, with_albums=False, prefetched=artist_prefetch) for name in artists],
            'album': [map_album(alb, with_songs=False, song_counts=song_counts) for alb in albums],
            'song': [map_song(s) for s in songs]
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)