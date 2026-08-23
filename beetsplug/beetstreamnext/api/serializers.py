from typing import TYPE_CHECKING, Optional, Tuple, Dict, List, Any, Sequence
import os
import flask
from beets.library import LibModel, Item

from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.utils.general import timestamp_to_iso, genres_formatter
from beetsplug.beetstreamnext.utils.text import split_beets_multi, validate_mbid
from beetsplug.beetstreamnext.utils.system import get_mimetype
from beetsplug.beetstreamnext.utils.db import chunked_query
from beetsplug.beetstreamnext.core.external import query_musicbrainz, query_discogs
from beetsplug.beetstreamnext.core.database import write_beets_field
from beetsplug.beetstreamnext.core.cache import preload_songs, preload_albums, one_rating, one_like, one_play_stats, avg_rating
from beetsplug.beetstreamnext.core.images import image_url
from beetsplug.beetstreamnext.settings import settings_store
from beetsplug.beetstreamnext.api.idmapper import IDMapper, standardise_datadict, _get_artist_metadata

if TYPE_CHECKING:
    from beetsplug.beetstreamnext.core.playlists import Playlist


def commit_likes(subsonic_id: str, key: str, value: Any) -> None:
    """
    Apply one user's Likes/Rating value to Beets's db. Only one user can be applied because Beets
    is single-user.
    Artists/playlists/radio stations have no single Beets row to attach a value to, so
    they are silently skipped.
    """
    id_type = IDMapper.get_type(subsonic_id)

    if id_type == 'song':
        song = IDMapper.resolve_song(subsonic_id)
        entity_type, beets_id = 'item', (song.id if song else None)
    elif id_type == 'album':
        album = IDMapper.resolve_album(subsonic_id)
        entity_type, beets_id = 'album', (album.id if album else None)
    else:
        return

    if beets_id is None:
        return

    try:
        write_beets_field(entity_type, beets_id, key, value, allow_flex=True)
    except Exception as e:
        bsn_logger.warning(f"Failed to mirror '{key}' to beets {entity_type} {beets_id}: {e}")


def map_media(beets_object: Dict | LibModel) -> dict:

    data = standardise_datadict(beets_object)

    track_artist_name = data.get('artist') or data.get('albumartist') or ''

    main_ar_name = data.get('albumartist') or data.get('artist') or ''
    main_ar_mbid = validate_mbid(data.get('mb_albumartistid')) or validate_mbid(data.get('mb_artistid'))

    artist_id = IDMapper.mint_artist(main_ar_mbid or main_ar_name, is_mbid=bool(main_ar_mbid))

    artists, album_artists, contributors, display_composer = get_artists(data)

    raw_genres = f"{data.get('genres') or ''};{data.get('genre') or ''}"
    formatted_genres = genres_formatter(raw_genres)

    main_genre = formatted_genres[0] if formatted_genres else ''
    genres_list = [{'name': g} for g in formatted_genres]

    subsonic_media = {
        'artist': track_artist_name,
        'artistId': artist_id,
        'displayArtist': track_artist_name,
        'displayAlbumArtist': main_ar_name,
        'artists': artists,
        'albumArtists': album_artists,
        'contributors': contributors,
        'displayComposer': display_composer,
        'album': data.get('album') or '',
        'year': data.get('year') or 0,
        'genre': main_genre,
        'genres': genres_list,
        'created': timestamp_to_iso(data.get('added')),
        'originalReleaseDate': {
            'year': data.get('original_year') or data.get('year') or 0,
            'month': data.get('original_month') or data.get('month') or 0,
            'day': data.get('original_day') or data.get('day') or 0
        },
        'releaseDate': {
            'year': data.get('year') or 0,
            'month': data.get('month') or 0,
            'day': data.get('day') or 0
        },
    }

    if display_composer:
        subsonic_media['displayComposer'] = display_composer

    return subsonic_media


def map_album(album_object: Dict | LibModel, include_songs: bool = True, song_counts: Optional[Dict] = None) -> dict:

    data = standardise_datadict(album_object)

    beets_album_id = data.get('id', 0)
    subsonic_album_id = IDMapper.mint_album(beets_album_id)
    album_name = data.get('album', '')

    subsonic_album = map_media(data)

    album_specific = {
        'id': subsonic_album_id,
        'musicBrainzId': validate_mbid(data.get('mb_albumid')),
        'name': album_name,
        'sortName': album_name,
        'coverArt': subsonic_album_id,
        'userRating': one_rating(subsonic_album_id),
        'isCompilation': bool(data.get('comp', False)),

        # These are only needed when part of a directory response
        'isDir': True,
        'parent': subsonic_album['artistId'],

        # Title field is required for Child responses (also used in albumList or albumList2 responses)
        'title': album_name,

        # This is only needed when part of a Child response
        'mediaType': 'album'
    }
    subsonic_album.update(album_specific)

    version = data.get('version')  # 'Deluxe Edition', 'Japanese Expanded Edition', etc.
    if not version and subsonic_album['musicBrainzId'] and app.config.get('fetch_album_version'):
        mb_data = query_musicbrainz(subsonic_album['musicBrainzId'], data_type='album')
        version = mb_data.get('disambiguation')

    if version:
        subsonic_album['version'] = version
        if app.config.get('save_album_version'):
            write_beets_field('album', data['id'], 'version', version, allow_flex=True)

    # Add labels if possible
    label = data.get('label', '')
    if label:
        subsonic_album['recordLabels'] = [{'name': label}]

    # Add release types if possible
    rt = data.get('albumtypes', '') or data.get('albumtype', '')
    release_types = [s.title() for s in split_beets_multi(rt)]
    if release_types:
        subsonic_album['releaseTypes'] = release_types

    # Add multi-disc info if needed
    nb_discs = data.get('disctotal', 1)
    if nb_discs > 1:
        subsonic_album["discTitles"] = [
            {'disc': d, 'title': ' - '.join(filter(None, [data.get('album', None), f'Disc {d + 1}']))}
            for d in range(nb_discs)
        ]

    # Songs should be included when in:
    # - AlbumID3WithSongs response
    # - directory response ('song' key needs to be renamed to 'child')

    if song_counts and beets_album_id in song_counts:
        subsonic_album['songCount'], subsonic_album['duration'] = song_counts[beets_album_id]

    elif not include_songs:
        # No need for full song objects, only SQL count
        with flask.g.lib.transaction() as tx:
            rows = tx.query(
                """
                SELECT COUNT(*), SUM(length)
                FROM items
                WHERE album_id = ?
                """, (beets_album_id,)
            )

        if rows:
            count, duration = rows[0][:2]
            subsonic_album['songCount'] = count
            subsonic_album['duration'] = round(duration or 0)
        else:
            subsonic_album['songCount'] = 0
            subsonic_album['duration'] = 0

    if include_songs:
        # Need song details
        songs = list(flask.g.lib.items(f'album_id:{beets_album_id}'))

        preload_songs(songs)

        if 'songCount' not in subsonic_album:
            subsonic_album['songCount'] = len(songs)
            subsonic_album['duration'] = round(sum(s.get('length', 0) for s in songs))

        song_filesizes = {}
        if songs:
            try:
                album_dir = os.path.dirname(os.fsdecode(songs[0].path))
                with os.scandir(album_dir) as it:
                    for entry in it:
                        if entry.is_file():
                            song_filesizes[entry.path] = entry.stat().st_size
            except Exception as e:
                bsn_logger.debug(f"Filesize prefetch failed: {e}")

        songs.sort(key=lambda s: (s.get('disc', 1), s.get('track', 1)))
        subsonic_album['song'] = [map_song(s, prefetched_sizes=song_filesizes) for s in songs]

    local_avg, local_count = avg_rating(subsonic_album_id)
    discogs_mode = app.config.get('discogs_ratings', 'off')

    discogs_avg = None
    if discogs_mode != 'off' and data.get('discogs_albumid') and (discogs_mode == 'prefer' or not local_count):
        rating = query_discogs(data['discogs_albumid']).get('community', {}).get('rating', {})
        if rating.get('average'):
            discogs_avg = round(rating['average'], 2)

    if discogs_mode == 'prefer' and discogs_avg is not None:
        subsonic_album['averageRating'] = discogs_avg
    elif local_count:
        subsonic_album['averageRating'] = local_avg
    elif discogs_avg is not None:
        subsonic_album['averageRating'] = discogs_avg
    else:
        subsonic_album['averageRating'] = 0

    # Starred status
    liked_at = one_like(subsonic_album_id)
    if liked_at:
        subsonic_album['starred'] = timestamp_to_iso(liked_at)

    return subsonic_album


def map_song(song_object: Dict | LibModel | Item, prefetched_sizes: Optional[Dict[str, int]] = None) -> dict:

    data = standardise_datadict(song_object)

    song_id = IDMapper.mint_song(data)
    song_title = data.get('title') or ''

    subsonic_song = map_media(data)

    song_filepath = os.fsdecode(data.get('path', b''))
    album_id = IDMapper.mint_album(data.get('album_id', 0))

    song_specific = {
        'id': song_id,
        'musicBrainzId': validate_mbid(data.get('mb_releasetrackid')) or validate_mbid(data.get('mb_trackid')),
        'name': song_title,
        'sortName': song_title,
        'albumId': album_id,
        'coverArt': album_id or song_id,
        'language': data.get('language') or '',
        'path': song_filepath,
        'userRating': one_rating(song_id),
        'duration': round(data.get('length') or 0),
        'bpm': data.get('bpm') or 0,
        'bitRate': round((data.get('bitrate') or 0) / 1000),
        'bitDepth': data.get('bitdepth') or 0,
        'samplingRate': data.get('samplerate') or 0,
        'channelCount': data.get('channels') or 2,
        'discNumber': data.get('disc') or 1,
        'comment': data.get('comment') or '',

        # These are only needed when part of a directory response
        'isDir': False,
        'parent': album_id or subsonic_song['artistId'],

        'isVideo': False,
        'type': 'music',

        # Title field is required for Child responses
        'title': song_title,

        # This is only needed when part of a Child response
        'mediaType': 'song'
    }
    subsonic_song.update(song_specific)

    isrc_raw = data.get('isrc') or ''
    if isrc_raw:
        subsonic_song['isrc'] = split_beets_multi(isrc_raw)

    work = data.get('work') or ''
    if work:
        work_obj = {'name': work}
        mb_workid = data.get('mb_workid')
        if mb_workid:
            work_obj['musicBrainzId'] = mb_workid
        subsonic_song['works'] = [work_obj]

    tg = data.get('rg_track_gain')
    ag = data.get('rg_album_gain')

    # r128 fields are stored as LU/dB * 256
    if tg is None:
        r128_tg = data.get('r128_track_gain')
        if r128_tg is not None:
            tg = float(r128_tg) / 256.0

    if ag is None:
        r128_ag = data.get('r128_album_gain')
        if r128_ag is not None:
            ag = float(r128_ag) / 256.0

    # Peaks are stored as linear ratios 0.0 to 1.0
    tp = data.get('rg_track_peak')
    ap = data.get('rg_album_peak')

    if tg is not None or ag is not None:
        track_peak = min(max(float(tp or 1.0), 0.0), 1.0)
        album_peak = min(max(float(ap or 1.0), 0.0), 1.0)

        subsonic_song['replayGain'] = {
            'trackGain': round(float(tg or 0.0), 2),
            'albumGain': round(float(ag or 0.0), 2),
            'trackPeak': track_peak,
            'albumPeak': album_peak,
            'baseGain': 0.0
        }

    track_nb = data.get('track')
    if track_nb:
        subsonic_song['track'] = track_nb

    suffix = (data.get('format') or '').lower()
    if not suffix and song_filepath:
        suffix = song_filepath.rsplit('.', 1)[-1].lower()
    subsonic_song['suffix'] = suffix or 'mp3'
    subsonic_song['contentType'] = get_mimetype(song_filepath or suffix)

    if prefetched_sizes and song_filepath in prefetched_sizes:
        subsonic_song['size'] = prefetched_sizes[song_filepath]
    else:
        bitrate = data.get('bitrate') or 0
        length = data.get('length') or 0
        subsonic_song['size'] = round((bitrate * length) / 8)

        # only hit the disk if bitrate/length missing
        if subsonic_song['size'] == 0:
            try:
                subsonic_song['size'] = os.path.getsize(song_filepath)
            except Exception:
                pass

    stats = one_play_stats(song_id)
    if stats:
        subsonic_song['playCount'] = stats['play_count']
        if stats['last_played']:
            subsonic_song['played'] = timestamp_to_iso(stats['last_played'])

    liked_at = one_like(subsonic_song['id'])
    if liked_at:
        subsonic_song['starred'] = timestamp_to_iso(liked_at)

    return subsonic_song


def map_artist(artist_name: str, with_albums: bool = True, prefetched: Optional[Dict] = None) -> dict:

    # Priority: prefetched -> album query (when with_albums) -> standalone db query
    mbid = ''
    sort_name = artist_name
    album_count = 0
    albums = None

    if prefetched and artist_name in prefetched:
        pf = prefetched[artist_name]
        mbid = pf.get('mbid') or ''
        sort_name = pf.get('sort_name') or artist_name
        album_count = pf.get('album_count', 0)

    elif with_albums:
        albums = list(flask.g.lib.albums(f'albumartist:{artist_name}'))
        if albums:
            mbid = albums[0].get('mb_albumartistid', '') or ''
            sort_name = albums[0].get('albumartist_sort', '') or artist_name
        album_count = len(albums) if albums else 0

    else:
        with flask.g.lib.transaction() as tx:
            row = tx.query(
                """
                SELECT COUNT(*), mb_albumartistid, albumartist_sort
                FROM albums
                WHERE albumartist = ?
                GROUP BY albumartist
                """, (artist_name,)
            ).fetchone()

        if row:
            album_count, mbid, sort_name = row[0], row[1] or '', row[2] or artist_name

    meta = _get_artist_metadata(artist_name)
    mbid = validate_mbid(mbid) or meta['mbid']  # meta['mbid'] is already validated by _artist_metadata()
    sort_name = sort_name if sort_name != artist_name else meta['sort_name']
    roles = meta['roles']

    subsonic_artist_id = IDMapper.mint_artist(mbid or artist_name, is_mbid=bool(mbid))

    subsonic_artist = {
        'id': subsonic_artist_id,
        'name': artist_name,
        'sortName': sort_name,
        'roles': roles,
        'musicBrainzId': mbid,
        'title': artist_name,
        'albumCount': album_count,
        'coverArt': subsonic_artist_id,
        'userRating': one_rating(subsonic_artist_id),
        'artistImageUrl': image_url(subsonic_artist_id),
        'mediaType': 'artist'
    }

    if with_albums:

        if albums is None:  # already fetched above if not prefetched
            albums = list(flask.g.lib.albums(f'albumartist:{artist_name}'))

        preload_albums(albums)
        song_counts = get_song_counts(albums)

        subsonic_artist['album'] = [
            map_album(alb, include_songs=False, song_counts=song_counts)
            for alb in albums
        ]

    liked_at = one_like(subsonic_artist_id)
    if liked_at:
        subsonic_artist['starred'] = timestamp_to_iso(liked_at)

    return subsonic_artist


def map_playlist(playlist : 'Playlist', include_songs: bool = False) -> dict:
    subsonic_playlist = {
        'id': playlist.id,
        'name': playlist.name,
        'comment': playlist.comment,
        'songCount': playlist.song_count,
        'duration': playlist.duration,
        'created': timestamp_to_iso(playlist.ctime),
        'changed': timestamp_to_iso(playlist.mtime),
        'owner': playlist.owner or playlist.creator or '',
        'public': playlist.owner is None,
    }
    if include_songs and playlist.songs:
        subsonic_playlist['entry'] = playlist.songs

    return subsonic_playlist


def map_radio_station(row: dict) -> dict:
    station_id = IDMapper.mint_radio(row['id'])

    subsonic_radio_station = {
        'id': station_id,
        'name': row['name'],
        'streamUrl': row['stream_url'],
        'homePageUrl': row['homepage_url'] or '',
        'coverArt': station_id
    }
    return subsonic_radio_station


def map_share(row: dict, entries: Sequence[str]) -> dict:
    songs = []
    albums = []

    for entry_id in entries:
        entry_type = IDMapper.get_type(entry_id)

        if entry_type == 'song':
            item = IDMapper.resolve_song(entry_id)
            if item:
                songs.append(map_song(item))

        elif entry_type == 'album':
            alb = IDMapper.resolve_album(entry_id)
            if alb:
                albums.append(map_album(alb, include_songs=False))

    # Force public hostname (if set)
    external_host = settings_store.get('external_hostname')

    if external_host:
        scheme = 'https' if (flask.request.is_secure or settings_store.get('reverse_proxy')) else 'http'
        share_url = f"{scheme}://{external_host}{flask.url_for('public.share_view', share_id=row['id'])}"
    else:
        share_url = flask.url_for('public.share_view', share_id=row['id'], _external=True)

    subsonic_share = {
        'id': row['id'],
        'url': share_url,
        'description': row['description'] or '',
        'username': row['username'],
        'created': timestamp_to_iso(row['created']),
        'expires': timestamp_to_iso(row['expires']),
        'visitCount': row['visit_count'],
        'entry': songs + albums
    }
    return subsonic_share


##
# Other more specialised utils


def get_song_counts(albums: List[Dict]) -> dict:
    """Get song counts for a list of albums in a single db query."""

    album_ids = [row['id'] for row in albums]

    if album_ids:
        with (flask.g.lib.transaction() as tx):
            sql_query = ('SELECT album_id, COUNT(*) as count, CAST(SUM(length) AS INTEGER) as duration'
                         + ' FROM items WHERE album_id IN ({q}) GROUP BY album_id')
            count_rows = chunked_query(
                db_obj=tx,
                query_template=sql_query,
                chunked_values=album_ids
            )
        counts = {row['album_id']: (row['count'], row['duration'] or 0) for row in count_rows}
    else:
        counts = {}

    return counts


def get_artists(data: dict) -> Tuple[List[Dict], List[Dict], List[Dict], str]:
    artists_array = []
    album_artists_array = []
    contributors_array = []
    composers = []

    seen_artists = set()
    seen_album_artists = set()
    seen_contributors = set()

    def _process(raw_names: str, raw_mbids: str, target_list: list, seen_set: set, is_contributor: bool = False, role: str = ''):
        if not raw_names:
            return

        names = split_beets_multi(raw_names)
        mbids = split_beets_multi(raw_mbids) if raw_mbids else []

        for i, name in enumerate(names):
            if not name:
                continue

            mbid = ''
            if i < len(mbids) and mbids[i]:
                mbid = validate_mbid(mbids[i])
            elif is_contributor:
                meta = _get_artist_metadata(name)
                mbid = meta['mbid']

            contributor_id = IDMapper.mint_artist(mbid or name, is_mbid=bool(mbid))
            if is_contributor:
                dedup_key = (contributor_id, role)
                if dedup_key not in seen_set:
                    seen_set.add(dedup_key)
                    target_list.append({
                        'role': role,
                        'artist': {
                            'id': contributor_id,
                            'name': name
                        }
                    })
                    if role == 'composer':
                        composers.append(name)
            else:
                dedup_key = contributor_id
                if dedup_key not in seen_set:
                    seen_set.add(dedup_key)
                    target_list.append({
                        'id': contributor_id,
                        'name': name
                    })

    _process(data.get('artists') or '', data.get('mb_artistids') or '', artists_array, seen_artists)
    _process(data.get('albumartists') or '', data.get('mb_albumartistids') or '', album_artists_array, seen_album_artists)

    _process(data.get('composers') or data.get('composer') or '', '', contributors_array, seen_contributors, True, 'composer')
    _process(data.get('lyricists') or data.get('lyricist') or '', '', contributors_array, seen_contributors, True, 'lyricist')
    _process(data.get('remixers') or data.get('remixer') or '', '', contributors_array, seen_contributors, True, 'remixer')
    _process(data.get('arrangers') or data.get('arranger') or '', '', contributors_array, seen_contributors, True, 'arranger')

    display_composer = ", ".join(composers)

    return artists_array, album_artists_array, contributors_array, display_composer