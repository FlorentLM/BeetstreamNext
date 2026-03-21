import time
import urllib.parse
from collections import defaultdict
from functools import partial
import flask

from beetsplug.beetstreamnext import app, _nb_items_lock
from beetsplug.beetstreamnext.utils import (
    subsonic_response,
    sub_to_beets_artist,
    map_artist, map_album,
    query_deezer, query_lastfm,
    trim_text, remove_accents, query_wikipedia, WIKI_API
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
        albums = flask.g.lib.albums(f'albumartist:{artist_name}')
                                     # I don't think there is any endpoint that returns an artist with albums AND songs?
        payload['artist']['album'] = list(map(partial(map_album, with_songs=False), albums))

    return payload


@app.route('/rest/getArtists', methods=["GET", "POST"])
@app.route('/rest/getArtists.view', methods=["GET", "POST"])

@app.route('/rest/getIndexes', methods=["GET", "POST"])
@app.route('/rest/getIndexes.view', methods=["GET", "POST"])
def get_artists_or_indexes():
    r = flask.request.values

    modified_since = r.get('ifModifiedSince', '')

    with flask.g.lib.transaction() as tx:
        artists = [row[0] for row in tx.query("SELECT DISTINCT albumartist FROM albums WHERE albumartist is NOT NULL")]

    alphanum_dict = defaultdict(list)
    for artist in artists:
        alphanum_dict[remove_accents(artist[0]).upper()].append(artist)

    tag = 'indexes' if flask.request.path.rsplit('.', 1)[0].endswith('Indexes') else 'artists'
    payload = {
        tag: {
            'ignoredArticles': '',      # TODO - include config from 'the' plugin??
            'index': [
                {'name': char, 'artist': list(map(map_artist, artists))}
                for char, artists in sorted(alphanum_dict.items())
            ]
        }
    }

    if tag == 'indexes':
        with flask.g.lib.transaction() as tx:
            latest = int(tx.query("SELECT added FROM items ORDER BY added DESC LIMIT 1")[0][0])
            nb_items = tx.query("SELECT COUNT(*) FROM items")[0][0]

        with _nb_items_lock:
            if nb_items < app.config['nb_items']:
                app.logger.warning('Media deletion detected (or very first time getIndexes is queried)')
                latest = int(time.time() * 1000)
                app.config['nb_items'] = nb_items

        payload[tag]['lastModified'] = latest

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

    artist_name = sub_to_beets_artist(r.get('id'))
    first_item = flask.g.lib.items(f'albumartist:{artist_name}')[0]
    artist_mbid = first_item.get('mb_albumartistid', '')

    short_bio = ''

    if app.config['lastfm_api_key']:
        data_lastfm = query_lastfm(artist_mbid, 'artist')
        lastfm_bio = data_lastfm.get('artist', {}).get('bio', {}).get('content', '')

        if lastfm_bio:
            short_bio = trim_text(lastfm_bio, char_limit=300)

    if not short_bio and WIKI_API:
        wiki_bio = query_wikipedia(artist_name)
        if wiki_bio:
            short_bio = trim_text(wiki_bio, char_limit=300)

    if not short_bio:
        short_bio = f'wow. much artist. very {artist_name}'

    tag = 'artistInfo2' if flask.request.path.rsplit('.', 1)[0].endswith('2') else 'artistInfo'
    payload = {
        tag: {
            'biography': short_bio,
            'musicBrainzId': artist_mbid,
            'lastFmUrl': f"https://www.last.fm/music/{urllib.parse.quote_plus(artist_name.replace(' ', '+'))}",
        }
    }

    if app.config['fetch_artists_images']:
        # TODO - this is not fetching the actual images, maybe we keep it as always on?
        dz_data = query_deezer(artist=artist_name)

        if dz_data and dz_data.get('type', '') == 'artist':
            payload[tag]['smallImageUrl'] = dz_data.get('picture_medium', ''),
            payload[tag]['mediumImageUrl'] = dz_data.get('picture_big', ''),
            payload[tag]['largeImageUrl'] = dz_data.get('picture_xl', '')

    return subsonic_response(payload, r.get('f', 'xml'))