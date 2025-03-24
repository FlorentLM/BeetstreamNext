from beetsplug.beetstream.utils import *
from beetsplug.beetstream import app
import time
from collections import defaultdict
from functools import partial
import flask


def artist_payload(subsonic_artist_id: str, with_albums=True) -> dict:

    artist_name = stb_artist(subsonic_artist_id)

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
    # TODO

    r = flask.request.values

    artist_name = stb_artist(r.get('id'))

    payload = {
        "artistInfo2": {
            "biography": f"wow. much artist. very {artist_name}",
            "musicBrainzId": "",
            "lastFmUrl": "",
            "smallImageUrl": "",
            "mediumImageUrl": "",
            "largeImageUrl": ""
        }
    }

    return subsonic_response(payload, r.get('f', 'xml'))