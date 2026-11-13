from __future__ import annotations

import os
import time
import urllib.parse
from collections import defaultdict
import flask

from .. import subsonic_bp

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.utils.text import remove_accents, trim_text, safe_str, strip_article, strip_html, validate_mbid
from beetsplug.beetstreamnext.utils.general import api_bool, page_size
from beetsplug.beetstreamnext.core.services.external.lastfm import query_lastfm
from beetsplug.beetstreamnext.core.services.external.wikipedia import query_wikipedia
from beetsplug.beetstreamnext.core.services.external.musicbrainz import query_wikidata_title
from beetsplug.beetstreamnext.core.storage.userprefs_cache import preload_artists
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.core.library.exclusions import exclusions
from beetsplug.beetstreamnext.core.media.images import tokenised_image_url
from beetsplug.beetstreamnext.blueprints.subsonic.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.core.library.indexes import Artist, ArtistIndex
from beetsplug.beetstreamnext.core.library.resolve import Resolve
from beetsplug.beetstreamnext.core.library.serialise import Serialise
from beetsplug.beetstreamnext.core.config.settings_schema import SETTINGS_SCHEMA


def artist_payload(subsonic_artist_id: str, with_albums: bool = True) -> dict:

    value, _ = IDs.decode_artist(subsonic_artist_id)
    if not value:
        return {}

    artist = Resolve.artist(subsonic_artist_id)
    if not artist:
        return {}

    return {'artist': Serialise.artist(artist, with_albums=with_albums)}


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getArtists/
@subsonic_bp.route('/getArtists', methods=['GET', 'POST'])
@subsonic_bp.route('/getArtists.view', methods=['GET', 'POST'])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getIndexes/
@subsonic_bp.route('/getIndexes', methods=['GET', 'POST'])
@subsonic_bp.route('/getIndexes.view', methods=['GET', 'POST'])
def endpoint_get_artists_or_indexes() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    modified_since = r.get('ifModifiedSince', default=0, type=int)

    tag = 'indexes' if 'getIndexes' in flask.request.path else 'artists'

    # Beets db modification time, or when user hides something
    lib_path = flask.g.lib.path
    latest_mtime = int(os.path.getmtime(os.fsdecode(lib_path)) * 1000)

    with database() as db:
        hidden_changed = db.execute(
            """
            SELECT MAX(changed) FROM (
                SELECT changed_at AS changed FROM user_changes WHERE username = ? AND kind = 'exclusions'
                UNION ALL
                SELECT updated_at FROM settings WHERE key IN ('hide_dislikes', 'hide_low_ratings', 'low_rating_cutoff', 'high_rating_cutoff')
            )
            """, (flask.g.username,)
        ).fetchone()[0]

    latest_mtime = max(latest_mtime, int((hidden_changed or 0) * 1000))

    exc = exclusions()
    hiding = exc.active(scope='browse')

    # includeDisliked changes the content without changing latest_mtime
    if modified_since and 'includeDisliked' not in r:
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

    artists = [
        artist for artist in ArtistIndex.get().album_artists
        if not (hiding and exc.artist_hidden(artist.id, scope='browse'))
    ]

    ignored_articles = app.config.get('ignored_articles', SETTINGS_SCHEMA['ignored_articles']['default'])
    articles = ignored_articles.split()

    alphanum_dict = defaultdict(list)
    for artist in artists:
        char = remove_accents(strip_article(artist.sort_name, articles)[0]).upper()
        group_key = char if char.isalpha() else '#'
        alphanum_dict[group_key].append(artist)

    preload_artists(artists)

    payload = {
        tag: {
            'index': [
                {
                    'name': char,
                    'artist': [Serialise.artist(a, with_albums=False) for a in artists]
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
@subsonic_bp.route('/getArtist', methods=['GET', 'POST'])
@subsonic_bp.route('/getArtist.view', methods=['GET', 'POST'])
def endpoint_get_artist() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    artist_id = r.get('id', default='', type=safe_str)   # Required

    payload = artist_payload(artist_id, with_albums=True)   # getArtist endpoint needs to include albums
    if not payload:
        return subsonic_error(70, resp_fmt=resp_fmt)

    albums = payload['artist'].get('album')
    if albums:
        payload['artist']['album'] = exclusions().album_entries(albums, scope='browse', inherit=False)

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getArtistInfo/
@subsonic_bp.route('/getArtistInfo', methods=['GET', 'POST'])
@subsonic_bp.route('/getArtistInfo.view', methods=['GET', 'POST'])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getArtistInfo2/
@subsonic_bp.route('/getArtistInfo2', methods=['GET', 'POST'])
@subsonic_bp.route('/getArtistInfo2.view', methods=['GET', 'POST'])
def endpoint_artist_info() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    artist_id = r.get('id', default='', type=safe_str)   # Required
    count = r.get('count', default=20, type=page_size)
    include_not_present = r.get('includeNotPresent', default=False, type=api_bool)

    if not artist_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    artist = Resolve.artist(artist_id)
    if not artist:
        return subsonic_error(70, resp_fmt=resp_fmt)

    short_bio = ''

    if app.config['lastfm_api_key']:
        if artist.mbid:
            data_lastfm = query_lastfm(q=artist.mbid, data_type='artist', method='info', is_mbid=True)
        else:
            data_lastfm = query_lastfm(q=artist.name, data_type='artist', method='info', is_mbid=False)

        lastfm_bio = strip_html(data_lastfm.get('artist', {}).get('bio', {}).get('content', ''))     # lastfm community-edited has HTML

        bio_lower = lastfm_bio.lower()
        if lastfm_bio and (
            'is an incorrect tag for' in bio_lower
            or 'more than one artist with this name' in bio_lower
        ):
            lastfm_bio = ''

        if lastfm_bio:
            short_bio = trim_text(lastfm_bio, char_limit=300)

    if not short_bio and app.config.get('fetch_artists_biographies'):
        wiki_title = (query_wikidata_title(artist.mbid) if artist.mbid else None) or artist.name
        wiki_bio = query_wikipedia(wiki_title, _cache_ttl_hash=round(time.time() / 3600))
        if wiki_bio:
            short_bio = trim_text(wiki_bio, char_limit=300)

    if not short_bio:
        short_bio = f'wow. much artist. very {artist.name}'

    tag = 'artistInfo2' if 'getArtistInfo2' in flask.request.path else 'artistInfo'

    image_id = artist.id      # Input may have been a song or an album

    payload = {
        tag: {
            'biography': short_bio,
            'musicBrainzId': artist.mbid,
            'lastFmUrl': f"https://www.last.fm/music/{urllib.parse.quote_plus(artist.name.replace(' ', '+'))}",
            'largeImageUrl': tokenised_image_url(image_id, size=1200),
            'mediumImageUrl': tokenised_image_url(image_id, size=500),
            'smallImageUrl': tokenised_image_url(image_id, size=250)
        }
    }

    if app.config['lastfm_api_key'] and count > 0:
        if artist.mbid:
            data_similar = query_lastfm(q=artist.mbid, data_type='artist', method='similar', is_mbid=True)
        else:
            data_similar = query_lastfm(q=artist.name, data_type='artist', method='similar', is_mbid=False)

        index = ArtistIndex.get()
        similar_artists = []
        for entry in data_similar.get('similarartists', {}).get('artist', []):
            name = entry.get('name')
            if not name:
                continue

            # Last.fm's MBID when library has it, otherwise the name
            similar_mbid = validate_mbid(entry.get('mbid'))
            similar = (index.by_id.get(IDs.encode_artist(similar_mbid, is_mbid=True)) if similar_mbid else None) or index.find(name)

            if similar is None or not similar.album_ids:
                if not include_not_present or index.named(name):
                    continue
                similar = Artist.bare(name)

            similar_artists.append(Serialise.artist(similar, with_albums=False))
            if len(similar_artists) >= count:
                break

        if similar_artists:
            payload[tag]['similarArtist'] = similar_artists

    return subsonic_response(payload, resp_fmt=resp_fmt)