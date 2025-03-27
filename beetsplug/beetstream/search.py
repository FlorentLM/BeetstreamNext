from beetsplug.beetstream.utils import *
from beetsplug.beetstream import app
from functools import partial


@app.route('/rest/search2', methods=["GET", "POST"])
@app.route('/rest/search2.view', methods=["GET", "POST"])

@app.route('/rest/search3', methods=["GET", "POST"])
@app.route('/rest/search3.view', methods=["GET", "POST"])

def search(ver=None):
    r = flask.request.values

    song_count = int(r.get('songCount', 20))
    song_offset = int(r.get('songOffset', 0))
    album_count = int(r.get('albumCount', 20))
    album_offset = int(r.get('albumOffset', 0))
    artist_count = int(r.get('artistCount', 20))
    artist_offset = int(r.get('artistOffset', 0))

    query = r.get('query', '')
    # Remove surrounding quotes if present
    if query.startswith('"') and query.endswith('"'):
        query = query[1:-1]

    if not query:
        if ver == 2:
            # search2 does not support empty queries: return an empty response
            return subsonic_response({}, r.get('f', 'xml'), failed=True)

        # search3 "must support an empty query and return all the data"
        # https://opensubsonic.netlify.app/docs/endpoints/search3/
        pattern = "%"
    else:
        pattern = f"%{query.lower()}%"

    with flask.g.lib.transaction() as tx:
        songs = list(tx.query(
            "SELECT * FROM items WHERE lower(title) LIKE ? ORDER BY title LIMIT ? OFFSET ?",
            (pattern, song_count, song_offset)
        ))
        albums = list(tx.query(
            "SELECT * FROM albums WHERE lower(album) LIKE ? ORDER BY album LIMIT ? OFFSET ?",
            (pattern, album_count, album_offset)
        ))
        artists = [row[0] for row in tx.query(
            """SELECT DISTINCT albumartist FROM albums WHERE lower(albumartist) LIKE ? 
            and albumartist is NOT NULL LIMIT ? OFFSET ?""",
            (pattern, artist_count, artist_offset)
        )]

    # TODO - do the sort in the SQL query instead?
    artists.sort(key=lambda name: strip_accents(name).upper())

    if flask.request.path.rsplit('.', 1)[0][6:] == 'search2':
        tag = 'searchResult2'
    elif flask.request.path.rsplit('.', 1)[0][6:] == 'search3':
        tag = 'searchResult3'
    else:
        tag = 'searchResult'
    payload = {
        tag: {
            'artist': list(map(partial(map_artist, with_albums=False), artists)),  # no need to include albums twice
            'album': list(map(partial(map_album, with_songs=False), albums)), # no need to include songs twice
            'song': list(map(map_song, songs))
        }
    }
    return subsonic_response(payload, r.get('f', 'xml'))
