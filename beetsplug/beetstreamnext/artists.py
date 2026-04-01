import os
import urllib.parse
from collections import defaultdict
from functools import partial
import flask

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.albums import get_song_counts
from beetsplug.beetstreamnext.utils import (
    subsonic_response,
    sub_to_beets_artist,
    map_artist, map_album, imageart_url,
    query_lastfm, query_wikipedia, WIKI_API,
    trim_text, remove_accents, safe_str
)


def artist_payload(subsonic_artist_id: str, with_albums=True) -> dict:

    value, is_mbid = sub_to_beets_artist(subsonic_artist_id)

    prefetched_albums = None

    if is_mbid:
        if with_albums:
            # fetch albums by mbid to get name and album list in 1 query
            with flask.g.lib.transaction() as tx:
                prefetched_albums = list(tx.query(
                    """
                    SELECT * FROM albums 
                    WHERE mb_albumartistid = ?
                    """, (value,)
                ))
            artist_name = prefetched_albums[0]['albumartist'] if prefetched_albums else value
        else:
            with flask.g.lib.transaction() as tx:
                rows = tx.query(
                    """
                    SELECT albumartist 
                    FROM albums 
                    WHERE mb_albumartistid = ? 
                    LIMIT 1
                    """, (value,)
                )
            artist_name = rows[0][0] if rows else value
    else:
        artist_name = value

    payload = {
        "artist": {
            "id": subsonic_artist_id,
            "name": artist_name,
        }
    }

    # When part of a directory response or a ArtistWithAlbumsID3 response
    if with_albums:
        if prefetched_albums is not None:
            albums = prefetched_albums
        else:
            albums = list(flask.g.lib.albums(f'albumartist:{artist_name}'))

        song_counts = get_song_counts(albums)

        payload['artist']['album'] = list(map(partial(map_album, include_songs=False, song_counts=song_counts), albums))

    return payload


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getArtists/
@app.route('/rest/getArtists', methods=["GET", "POST"])
@app.route('/rest/getArtists.view', methods=["GET", "POST"])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getIndexes/
@app.route('/rest/getIndexes', methods=["GET", "POST"])
@app.route('/rest/getIndexes.view', methods=["GET", "POST"])
def endpoint_get_artists_or_indexes():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    modified_since = r.get('ifModifiedSince', default=0, type=int)

    tag = 'indexes' if 'getIndexes' in flask.request.path else 'artists'

    # Beets db modification time
    lib_path = flask.g.lib.path
    latest_mtime = int(os.path.getmtime(os.fsdecode(lib_path)) * 1000)

    if modified_since:
        try:
            if latest_mtime <= modified_since:
                # library hasn't changed: return empty payload
                empty_payload = {
                    tag: {}
                }
                if tag == 'indexes':
                    empty_payload[tag]['lastModified'] = latest_mtime
                return subsonic_response(empty_payload, resp_fmt=resp_fmt)

        except ValueError:
            pass  # Client sent malformed timestamp, ignore and continue to full sync

    with flask.g.lib.transaction() as tx:
        rows = tx.query(
            """
            SELECT albumartist, COUNT(*) as album_count, mb_albumartistid
            FROM albums
            WHERE albumartist IS NOT NULL
            GROUP BY albumartist
            """
        )

    artist_prefetch = {}
    artists = []
    for row in rows:
        name, count, mbid = row[0], row[1], row[2]
        artists.append(name)
        artist_prefetch[name] = {'album_count': count, 'mbid': mbid}

    alphanum_dict = defaultdict(list)
    for artist in artists:
        if artist:
            char = remove_accents(artist[0]).upper()
            group_key = char if char.isalpha() else '#'
            alphanum_dict[group_key].append(artist)

    payload = {
        tag: {
            'index': [
                {
                    'name': char,
                    'artist': [map_artist(a, with_albums=False, prefetched=artist_prefetch) for a in artists]
                }
                for char, artists in sorted(alphanum_dict.items())
            ]
        }
    }

    ignored_articles = "The An A El La Los Las Le Les Die Das Ein Eine"
    # the_plugin = 'the' in config['plugins'].as_str_seq()
    # TODO: use config from 'the' plugin
    payload[tag]['ignoredArticles'] = ignored_articles

    if tag == 'indexes':
        payload[tag]['lastModified'] = latest_mtime

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getArtist/
@app.route('/rest/getArtist', methods=["GET", "POST"])
@app.route('/rest/getArtist.view', methods=["GET", "POST"])
def endpoint_get_artist():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    artist_id = r.get('id', default='', type=safe_str)   # Required

    payload = artist_payload(artist_id, with_albums=True)   # getArtist endpoint needs to include albums

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getArtistInfo/
@app.route('/rest/getArtistInfo', methods=["GET", "POST"])
@app.route('/rest/getArtistInfo.view', methods=["GET", "POST"])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getArtistInfo2/
@app.route('/rest/getArtistInfo2', methods=["GET", "POST"])
@app.route('/rest/getArtistInfo2.view', methods=["GET", "POST"])
def endpoint_artist_info():
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    artist_id = r.get('id', default='', type=safe_str)   # Required
    # TODO: ID can be artist, album or song

    value, is_mbid = sub_to_beets_artist(artist_id)

    if is_mbid:
        artist_mbid = value
        with flask.g.lib.transaction() as tx:
            rows = tx.query(
                """
                SELECT albumartist 
                FROM albums 
                WHERE mb_albumartistid = ? 
                LIMIT 1
                """, (value,)
            )
        artist_name = rows[0][0] if rows else value
    else:
        artist_name = value
        items = flask.g.lib.items(f'albumartist:{artist_name}')
        artist_mbid = items[0].get('mb_albumartistid', '') if items else ''

    short_bio = ''

    if app.config['lastfm_api_key']:
        if artist_mbid:
            data_lastfm = query_lastfm(q=artist_mbid, type='artist', method='info', mbid=True)
        else:
            data_lastfm = query_lastfm(q=artist_name, type='artist', method='info', mbid=False)

        lastfm_bio = data_lastfm.get('artist', {}).get('bio', {}).get('content', '')

        if lastfm_bio:
            short_bio = trim_text(lastfm_bio, char_limit=300)

    if not short_bio and WIKI_API:
        wiki_bio = query_wikipedia(artist_name)
        if wiki_bio:
            short_bio = trim_text(wiki_bio, char_limit=300)

    if not short_bio:
        short_bio = f'wow. much artist. very {artist_name}'

    tag = 'artistInfo2' if 'getArtistInfo2' in flask.request.path else 'artistInfo'
    payload = {
        tag: {
            'biography': short_bio,
            'musicBrainzId': artist_mbid,
            'lastFmUrl': f"https://www.last.fm/music/{urllib.parse.quote_plus(artist_name.replace(' ', '+'))}",
            'largeImageUrl': imageart_url(artist_id, size=1200),
            'mediumImageUrl': imageart_url(artist_id, size=500),
            'smallImageUrl': imageart_url(artist_id, size=250)
        }
    }

    return subsonic_response(payload, resp_fmt=resp_fmt)