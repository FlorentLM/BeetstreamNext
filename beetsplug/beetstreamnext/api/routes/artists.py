import os
import time
import urllib.parse
from collections import defaultdict
import flask

from .. import api_bp

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.utils.text import remove_accents, trim_text, safe_str, strip_article
from beetsplug.beetstreamnext.utils.general import api_bool
from beetsplug.beetstreamnext.core.external import query_lastfm, query_wikipedia
from beetsplug.beetstreamnext.core.cache import preload_artists
from beetsplug.beetstreamnext.core.images import image_url
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.core.mappings import IDs, Resolve, Serialise
from beetsplug.beetstreamnext.schemas import SETTINGS_SCHEMA


def artist_payload(subsonic_artist_id: str, with_albums: bool = True) -> dict:

    value, _ = IDs.decode_artist(subsonic_artist_id)
    if not value:
        return {}

    resolved = Resolve.artist(subsonic_artist_id)
    if not resolved:
        return {}

    artist_name, _ = resolved

    return {'artist': Serialise.artist(artist_name, with_albums=with_albums)}


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getArtists/
@api_bp.route('/getArtists', methods=['GET', 'POST'])
@api_bp.route('/getArtists.view', methods=['GET', 'POST'])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getIndexes/
@api_bp.route('/getIndexes', methods=['GET', 'POST'])
@api_bp.route('/getIndexes.view', methods=['GET', 'POST'])
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

    ignored_articles = app.config.get('ignored_articles', SETTINGS_SCHEMA['ignored_articles']['default'])
    articles = ignored_articles.split()

    alphanum_dict = defaultdict(list)
    for artist in artists:
        if artist:
            char = remove_accents(strip_article(artist, articles)[0]).upper()
            group_key = char if char.isalpha() else '#'
            alphanum_dict[group_key].append(artist)

    preload_artists(artist_prefetch)

    payload = {
        tag: {
            'index': [
                {
                    'name': char,
                    'artist': [Serialise.artist(a, with_albums=False, prefetched=artist_prefetch) for a in artists]
                }
                for char, artists in sorted(alphanum_dict.items())
            ]
        }
    }

    payload[tag]['ignoredArticles'] = ignored_articles

    if tag == 'indexes':
        payload[tag]['lastModified'] = latest_mtime

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getArtist/
@api_bp.route('/getArtist', methods=['GET', 'POST'])
@api_bp.route('/getArtist.view', methods=['GET', 'POST'])
def endpoint_get_artist() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    artist_id = r.get('id', default='', type=safe_str)   # Required

    payload = artist_payload(artist_id, with_albums=True)   # getArtist endpoint needs to include albums
    if not payload:
        return subsonic_error(70, resp_fmt=resp_fmt)

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getArtistInfo/
@api_bp.route('/getArtistInfo', methods=['GET', 'POST'])
@api_bp.route('/getArtistInfo.view', methods=['GET', 'POST'])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getArtistInfo2/
@api_bp.route('/getArtistInfo2', methods=['GET', 'POST'])
@api_bp.route('/getArtistInfo2.view', methods=['GET', 'POST'])
def endpoint_artist_info() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    artist_id = r.get('id', default='', type=safe_str)   # Required
    count = r.get('count', default=20, type=int)
    include_not_present = r.get('includeNotPresent', default=False, type=api_bool)

    if not artist_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    resolved = Resolve.artist(artist_id)
    if not resolved:
        return subsonic_error(70, resp_fmt=resp_fmt)

    artist_name, artist_mbid = resolved

    short_bio = ''

    if app.config['lastfm_api_key']:
        if artist_mbid:
            data_lastfm = query_lastfm(q=artist_mbid, data_type='artist', method='info', is_mbid=True)
        else:
            data_lastfm = query_lastfm(q=artist_name, data_type='artist', method='info', is_mbid=False)

        lastfm_bio = data_lastfm.get('artist', {}).get('bio', {}).get('content', '')

        if lastfm_bio:
            short_bio = trim_text(lastfm_bio, char_limit=300)

    if not short_bio and app.config.get('fetch_artists_biographies'):
        wiki_bio = query_wikipedia(artist_name, _cache_ttl_hash=round(time.time() / 3600))
        if wiki_bio:
            short_bio = trim_text(wiki_bio, char_limit=300)

    if not short_bio:
        short_bio = f'wow. much artist. very {artist_name}'

    tag = 'artistInfo2' if 'getArtistInfo2' in flask.request.path else 'artistInfo'

    # image id is the artist id, but input may have been song or album
    image_id = IDs.encode_artist(artist_mbid or artist_name, is_mbid=bool(artist_mbid))

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

    if app.config['lastfm_api_key'] and count > 0:
        if artist_mbid:
            data_similar = query_lastfm(q=artist_mbid, data_type='artist', method='similar', is_mbid=True)
        else:
            data_similar = query_lastfm(q=artist_name, data_type='artist', method='similar', is_mbid=False)

        similar_artists = []
        for entry in data_similar.get('similarartists', {}).get('artist', []):
            name = entry.get('name')
            if not name:
                continue
            mapped = Serialise.artist(name, with_albums=False)
            if not include_not_present and not mapped['albumCount']:
                continue
            similar_artists.append(mapped)
            if len(similar_artists) >= count:
                break

        if similar_artists:
            payload[tag]['similarArtist'] = similar_artists

    return subsonic_response(payload, resp_fmt=resp_fmt)