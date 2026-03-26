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
    trim_text, remove_accents
)


def artist_payload(subsonic_artist_id: str, with_albums=True) -> dict:

    artist_name = sub_to_beets_artist(subsonic_artist_id)

    payload = {
        "artist": {
            "id": subsonic_artist_id,
            "name": artist_name,
        }
    }

    # When part of a directory response or a ArtistWithAlbumsID3 response
    if with_albums:
        albums = list(flask.g.lib.albums(f'albumartist:{artist_name}'))
        # I don't think there is any endpoint that returns an artist with albums AND songs?
        song_counts = get_song_counts(albums)

        payload['artist']['album'] = list(map(partial(map_album, with_songs=False, song_counts=song_counts), albums))

    return payload


@app.route('/rest/getArtists', methods=["GET", "POST"])
@app.route('/rest/getArtists.view', methods=["GET", "POST"])

@app.route('/rest/getIndexes', methods=["GET", "POST"])
@app.route('/rest/getIndexes.view', methods=["GET", "POST"])
def get_artists_or_indexes():
    r = flask.request.values
    tag = 'indexes' if 'getIndexes' in flask.request.path else 'artists'

    # Beets db modification time
    lib_path = flask.g.lib.path
    if isinstance(lib_path, bytes):
        lib_path = lib_path.decode('utf-8')
    latest_mtime = int(os.path.getmtime(lib_path) * 1000)

    modified_since = r.get('ifModifiedSince')
    if modified_since:
        try:
            if latest_mtime <= int(modified_since):
                # library hasn't changed: return empty payload
                empty_payload = {tag: {}}
                if tag == 'indexes':
                    empty_payload[tag]['lastModified'] = latest_mtime

                return subsonic_response(empty_payload, r.get('f', 'xml'))
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
        alphanum_dict[remove_accents(artist[0]).upper()].append(artist)

    payload = {
        tag: {
            'ignoredArticles': '',      # TODO - include config from 'the' plugin??
            'index': [
                {
                    'name': char,
                    'artist': [map_artist(a, with_albums=False, prefetched=artist_prefetch) for a in artists]
                }
                for char, artists in sorted(alphanum_dict.items())
            ]
        }
    }

    if tag == 'indexes':
        payload[tag]['lastModified'] = latest_mtime

    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/getArtist', methods=["GET", "POST"])
@app.route('/rest/getArtist.view', methods=["GET", "POST"])
def get_artist():
    r = flask.request.values

    artist_id = r.get('id')
    payload = artist_payload(artist_id, with_albums=True)   # getArtist endpoint needs to include albums

    return subsonic_response(payload, r.get('f', 'xml'))


@app.route('/rest/getArtistInfo', methods=["GET", "POST"])
@app.route('/rest/getArtistInfo.view', methods=["GET", "POST"])

@app.route('/rest/getArtistInfo2', methods=["GET", "POST"])
@app.route('/rest/getArtistInfo2.view', methods=["GET", "POST"])
def artistInfo2():

    r = flask.request.values

    artist_id = r.get('id')
    artist_name = sub_to_beets_artist(artist_id)
    items = flask.g.lib.items(f'albumartist:{artist_name}')

    artist_mbid = items[0].get('mb_albumartistid', '') if items else ''
    short_bio = ''

    if app.config['lastfm_api_key']:
        data_lastfm = query_lastfm(q=artist_mbid, type='artist', method='info', mbid=True)
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

    return subsonic_response(payload, r.get('f', 'xml'))