import concurrent.futures
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.albums import get_song_counts
from beetsplug.beetstreamnext.utils import (
    subsonic_error, subsonic_response,
    remove_accents,
    map_artist, map_album, map_song, safe_str
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
    resp_fmt = r.get('f', default='xml', type=safe_str)

    # Pagination
    global_count = r.get('count', default=20, type=int)
    global_offset = r.get('offset', default=0, type=int)

    song_count = r.get('songCount', default=global_count, type=int)
    song_offset = r.get('songOffset', default=global_offset, type=int)
    album_count = r.get('albumCount', default=global_count, type=int)
    album_offset = r.get('albumOffset', default=global_offset, type=int)
    artist_count = r.get('artistCount', default=global_count, type=int)
    artist_offset = r.get('artistOffset', default=global_offset, type=int)

    # Legacy search
    depr_artist = r.get('artist', default='', type=safe_str)
    depr_album = r.get('album', default='', type=safe_str)
    depr_song = r.get('title', default='', type=safe_str)
    depr_any = r.get('any', default='', type=safe_str)
    newer_than = r.get('newerThan', default=0, type=int) / 1000.0

    # search2/3 query
    query_untrunc = r.get('query', default='', type=safe_str)
    if query_untrunc.startswith('"') and query_untrunc.endswith('"'):
        query_untrunc = query_untrunc[1:-1]
    query = query_untrunc[:256]

    if 'search2' in flask.request.path:
        tag = 'searchResult2'
    elif 'search3' in flask.request.path:
        tag = 'searchResult3'
    else:
        tag = 'searchResult'

    # Priority: query (modern) > any (legacy) > specific fields
    main_query = query or depr_any or ''

    if tag == 'searchResult2' and not any([main_query, depr_artist, depr_album, depr_song]):
        return subsonic_error(10, message='You must specify a query.', resp_fmt=resp_fmt)

    artist_prefetch = {}

    # Beets query
    if main_query.startswith(('b:', 'beets:')):
        clean_query = main_query.split(':', 1)[1].strip()

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
        lib = app.config.get('lib')

        def build_where(table_type: str, main_field: str):
            # table_type: 'items' or 'albums'
            # main_field: 'title' or 'album'
            conds = []
            params = []

            # Filter by main query/any
            if main_query:
                conds.append(f"lower({main_field}) LIKE ?")
                params.append(f"%{main_query.lower()}%")

            # Filter by legacy fields
            if depr_song and table_type == 'items':
                conds.append("lower(title) LIKE ?")
                params.append(f"%{depr_song.lower()}%")
            if depr_album:
                field = 'album'
                conds.append(f"lower({field}) LIKE ?")
                params.append(f"%{depr_album.lower()}%")
            if depr_artist:
                field = 'albumartist' if table_type == 'albums' else 'artist'
                conds.append(f"lower({field}) LIKE ?")
                params.append(f"%{depr_artist.lower()}%")

            # Filter by date (newerThan)
            if newer_than > 0:
                conds.append("added > ?")
                params.append(newer_than)

            where_clause = " WHERE " + " AND ".join(conds) if conds else ""
            return where_clause, params

        def search_songs():
            where, params = build_where('items', 'title')
            with lib.transaction() as tx:
                rows = tx.query(
                    f"""
                    SELECT * FROM items {where} 
                    ORDER BY title 
                    COLLATE NOCASE 
                    LIMIT ? OFFSET ?
                    """, params + [song_count, song_offset]
                )
            return list(rows)

        def search_albums():
            where, params = build_where('albums', 'album')
            with lib.transaction() as tx:
                rows = tx.query(
                    f"""
                    SELECT * FROM albums {where} 
                    ORDER BY album 
                    COLLATE NOCASE 
                    LIMIT ? OFFSET ?
                    """, params + [album_count, album_offset]
                )
            return list(rows)

        def search_artists():
            # Artists are derived from albums for indexing
            conds, params = [], []
            if main_query or depr_artist:
                q = main_query or depr_artist
                conds.append("lower(albumartist) LIKE ?")
                params.append(f"%{q.lower()}%")

            where = " WHERE " + " AND ".join(conds) if conds else " WHERE albumartist IS NOT NULL"

            with lib.transaction() as tx:
                rows = tx.query(
                    f"""
                    SELECT albumartist, COUNT(*), mb_albumartistid 
                    FROM albums {where}
                    GROUP BY albumartist
                    ORDER BY albumartist COLLATE NOCASE
                    LIMIT ? OFFSET ?
                    """, params + [artist_count, artist_offset]
                )
            return list(rows)

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