from beetsplug.beetstream.utils import *
from beetsplug.beetstream import app
import time
import urllib.parse
from collections import defaultdict
from functools import partial
import flask


def artist_payload(subsonic_artist_id: str, with_albums=True) -> dict:

    artist_name = sub_to_beets_artist(subsonic_artist_id)

    payload = {
        "artist": {
            "id": artist_id,
            "name": artist_name,
            "album": list(map(map_album, albums))
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
def get_artists():
    return _artists('artists')

@app.route('/rest/getIndexes', methods=["GET", "POST"])
@app.route('/rest/getIndexes.view', methods=["GET", "POST"])
def get_indexes():
    return _artists('indexes')

def _artists(version: str):
    r = flask.request.values

    modified_since = r.get('ifModifiedSince', '')

    with flask.g.lib.transaction() as tx:
        artists = [row[0] for row in tx.query("SELECT DISTINCT albumartist FROM albums WHERE albumartist is NOT NULL")]

    alphanum_dict = defaultdict(list)
    for artist in artists:
        alphanum_dict[strip_accents(artist[0]).upper()].append(artist)

    payload = {
        version: {
            'ignoredArticles': '',      # TODO - include config from 'the' plugin??
            'index': [
                {'name': char, 'artist': list(map(map_artist, artists))}
                for char, artists in sorted(alphanum_dict.items())
            ]
        }
    }

    if version == 'indexes':

        with flask.g.lib.transaction() as tx:
            latest = int(tx.query('SELECT added FROM items ORDER BY added DESC LIMIT 1')[0][0])
            # TODO - 'mtime' field?
            nb_items = tx.query('SELECT COUNT(*) FROM items')[0][0]

        if nb_items < app.config['nb_items']:
            app.logger.warning('Media deletion detected (or very first time getIndexes is queried)')
            # Deletion of items (or very first check since Beetstream started)
            latest = int(time.time() * 1000)
            app.config['nb_items'] = nb_items

        payload[version]['lastModified'] = latest

    return subsonic_response(payload, r.get('f', 'xml'))

@app.route('/rest/getArtist', methods=["GET", "POST"])
@app.route('/rest/getArtist.view', methods=["GET", "POST"])
def get_artist():
    r = flask.request.values

    artist_id = r.get('id')
    payload = artist_payload(artist_id, with_albums=True)   # getArtist endpoint needs to include albums

    return subsonic_response(payload, r.get('f', 'xml'))

@app.route('/rest/getArtistInfo2', methods=["GET", "POST"])
@app.route('/rest/getArtistInfo2.view', methods=["GET", "POST"])
def artistInfo2():

    r = flask.request.values

    artist_name = sub_to_beets_artist(r.get('id'))
    first_item = flask.g.lib.items(f'albumartist:{artist_name}')[0]
    artist_mbid = first_item.get('mb_albumartistid', '')

    if app.config['lastfm_api_key']:
        data_lastfm = query_lastfm(artist_mbid, 'artist')
        bio = data_lastfm.get('artist', {}).get('bio', {}).get('content', '')
        short_bio = trim_bio(bio, char_limit=300)
    else:
        short_bio = f'wow. much artist. very {artist_name}'

    payload = {
        'artistInfo2': {
            'biography': short_bio,
            'musicBrainzId': artist_mbid,
            'lastFmUrl': f'https://www.last.fm/music/{urllib.parse.quote_plus(artist_name.replace(' ', '+'))}',
        }
    }

    if app.config['fetch_artists_images']:
        # TODO - this is not fetching the actual images, maybe we keep it as always on?
        dz_query = urllib.parse.quote_plus(artist_name.replace(' ', '-'))
        dz_data = query_deezer(dz_query, 'artist')
        if dz_data:
            payload['artistInfo2']['smallImageUrl'] = dz_data.get('picture_medium', ''),
            payload['artistInfo2']['mediumImageUrl'] = dz_data.get('picture_big', ''),
            payload['artistInfo2']['largeImageUrl'] = dz_data.get('picture_xl', '')

    return subsonic_response(payload, r.get('f', 'xml'))