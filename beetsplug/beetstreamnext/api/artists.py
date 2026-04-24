import os
import time
import urllib.parse
from collections import defaultdict
from functools import partial
from typing import Dict

import flask

from . import api_bp

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.external import WIKI_API, query_lastfm, query_wikipedia
from beetsplug.beetstreamnext.userdata_caching import preload_artists
from beetsplug.beetstreamnext.utils import (
    subsonic_response, subsonic_error, trim_text, remove_accents, safe_str, beets_to_sub_artist, sub_to_beets_artist
)
from beetsplug.beetstreamnext.images import image_url
from beetsplug.beetstreamnext.mappings import resolve_artist, map_album, map_artist, get_song_counts


def artist_payload(subsonic_artist_id: str, with_albums: bool = True) -> Dict:

    value, is_mbid = sub_to_beets_artist(subsonic_artist_id)
    if not value:
        return {}

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
@api_bp.route('/getArtists', methods=["GET", "POST"])
@api_bp.route('/getArtists.view', methods=["GET", "POST"])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getIndexes/
@api_bp.route('/getIndexes', methods=["GET", "POST"])
@api_bp.route('/getIndexes.view', methods=["GET", "POST"])
def endpoint_get_artists_or_indexes() -> flask.Response:
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
            SELECT albumartist, COUNT(*) as album_count, mb_albumartistid, albumartist_sort
            FROM albums
            WHERE albumartist IS NOT NULL
            GROUP BY albumartist
            """
        )

    artist_prefetch = {}
    artists = []
    for row in rows:
        name, count, mbid, sort_name = row
        artists.append(name)
        artist_prefetch[name] = {'album_count': count, 'mbid': mbid, 'sort_name': sort_name}

    alphanum_dict = defaultdict(list)
    for artist in artists:
        if artist:
            char = remove_accents(artist[0]).upper()
            group_key = char if char.isalpha() else '#'
            alphanum_dict[group_key].append(artist)

    preload_artists(artist_prefetch)

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
@api_bp.route('/getArtist', methods=["GET", "POST"])
@api_bp.route('/getArtist.view', methods=["GET", "POST"])
def endpoint_get_artist() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    artist_id = r.get('id', default='', type=safe_str)   # Required

    payload = artist_payload(artist_id, with_albums=True)   # getArtist endpoint needs to include albums
    if not payload:
        return subsonic_error(70, resp_fmt=resp_fmt)

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getArtistInfo/
@api_bp.route('/getArtistInfo', methods=["GET", "POST"])
@api_bp.route('/getArtistInfo.view', methods=["GET", "POST"])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getArtistInfo2/
@api_bp.route('/getArtistInfo2', methods=["GET", "POST"])
@api_bp.route('/getArtistInfo2.view', methods=["GET", "POST"])
def endpoint_artist_info() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    artist_id = r.get('id', default='', type=safe_str)   # Required

    if not artist_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    resolved = resolve_artist(artist_id)
    if not resolved:
        return subsonic_error(70, resp_fmt=resp_fmt)

    artist_name, artist_mbid = resolved

    short_bio = ''

    if app.config['lastfm_api_key']:
        if artist_mbid:
            data_lastfm = query_lastfm(q=artist_mbid, type='artist', method='info', is_mbid=True)
        else:
            data_lastfm = query_lastfm(q=artist_name, type='artist', method='info', is_mbid=False)

        lastfm_bio = data_lastfm.get('artist', {}).get('bio', {}).get('content', '')

        if lastfm_bio:
            short_bio = trim_text(lastfm_bio, char_limit=300)

    if not short_bio and WIKI_API:
        wiki_bio = query_wikipedia(artist_name, cache_ttl_hash=round(time.time() / 3600))
        if wiki_bio:
            short_bio = trim_text(wiki_bio, char_limit=300)

    if not short_bio:
        short_bio = f'wow. much artist. very {artist_name}'

    tag = 'artistInfo2' if 'getArtistInfo2' in flask.request.path else 'artistInfo'

    # image id is the artist id, but input may have been song or album
    if artist_mbid:
        image_id = beets_to_sub_artist(artist_mbid)
    else:
        image_id = beets_to_sub_artist(artist_name, is_mbid=False)

    payload = {
        tag: {
            'biography': short_bio,
            'musicBrainzId': artist_mbid,
            'lastFmUrl': f"https://www.last.fm/music/{urllib.parse.quote_plus(artist_name.replace(' ', '+'))}",
            'largeImageUrl': image_url(image_id, size=1200),
            'mediumImageUrl': image_url(image_id, size=500),
            'smallImageUrl': image_url(image_id, size=250)
        }
    }

    return subsonic_response(payload, resp_fmt=resp_fmt)