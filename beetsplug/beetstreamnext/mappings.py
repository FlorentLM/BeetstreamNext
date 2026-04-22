import os
from typing import TYPE_CHECKING, Optional, Tuple, Dict, List, Any

import flask
from beets.library import LibModel, Item

from beetsplug.beetstreamnext import userdata_caching as userdata_caching, app
from beetsplug.beetstreamnext.utils import (
    get_mimetype, timestamp_to_iso,
    SNG_ID_PREF, sub_to_beets_song, beets_to_sub_song,
    ALB_ID_PREF, sub_to_beets_album, beets_to_sub_album,
    ART_ID_PREF, sub_to_beets_artist, beets_to_sub_artist,
    genres_formatter, split_beets_multi, chunked_query, imageart_url
)
if TYPE_CHECKING:
    from beetsplug.beetstreamnext.playlistprovider import Playlist


##

def standardise_datadict(obj: Dict | LibModel | Item | Any) -> Dict:
    """Standardise input (Beets Item/Album or sqlite3.Row) into a dict."""
    if isinstance(obj, LibModel):
        data = dict(obj)
        data['id'] = obj.id
        if hasattr(obj, 'path'):
            data['path'] = obj.path
        return data
    if isinstance(obj, dict):
        return obj
    try:
        return dict(obj)
    except (ValueError, TypeError):
        return {}


def map_media(beets_object: Dict | LibModel) -> Dict:

    data = standardise_datadict(beets_object)

    track_artist_name = data.get('artist') or data.get('albumartist') or ''

    main_artist_name = data.get('albumartist') or data.get('artist') or ''
    main_artist_mbid = data.get('mb_albumartistid') or data.get('mb_artistid') or ''

    if main_artist_mbid:
        artist_id = beets_to_sub_artist(main_artist_mbid)
    else:
        artist_id = beets_to_sub_artist(main_artist_name, is_mbid=False)

    artists, album_artists, contributors, display_composer = get_artists(data)

    raw_genres = f"{data.get('genres') or ''};{data.get('genre') or ''}"
    formatted_genres = genres_formatter(raw_genres)

    main_genre = formatted_genres[0] if formatted_genres else ''
    genres_list = [{'name': g} for g in formatted_genres]

    subsonic_media = {
        'artist': track_artist_name,
        'artistId': artist_id,
        'displayArtist': track_artist_name,
        'displayAlbumArtist': main_artist_name,
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


def map_album(album_object: Dict | LibModel, include_songs: bool = True, song_counts: Optional[Dict] = None) -> Dict:

    data = standardise_datadict(album_object)

    beets_album_id = data.get('id', 0)
    subsonic_album_id = beets_to_sub_album(beets_album_id)
    album_name = data.get('album', '')

    subsonic_album = map_media(data)

    album_specific = {
        'id': subsonic_album_id,
        'musicBrainzId': data.get('mb_albumid') or '',
        'name': album_name,
        'sortName': album_name,
        # 'version': 'Deluxe Edition', # TODO: items table has 'media' that contains "Vinyl", "CD", "Digital Media", etc
                        # TODO: also Musicbrainz puts stuff like "special collector's edition" in 'disambiguation'
        'coverArt': subsonic_album_id,
        'userRating': userdata_caching.one_rating(subsonic_album_id),
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

        userdata_caching.preload_songs(songs)

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
                app.logger.debug(f"Filesize prefetch failed: {e}")

        songs.sort(key=lambda s: (s.get('disc', 1), s.get('track', 1)))
        subsonic_album['song'] = [map_song(s, prefetched_sizes=song_filesizes) for s in songs]

    # Average rating
    if subsonic_album.get('song'):
        ratings = [s.get('userRating', 0) for s in subsonic_album['song'] if s.get('userRating', 0)]
        subsonic_album['averageRating'] = sum(ratings) / len(ratings) if ratings else 0
    else:
        subsonic_album['averageRating'] = album_specific['userRating']

    # Starred status
    liked_at = userdata_caching.one_like(subsonic_album_id)
    if liked_at:
        subsonic_album['starred'] = timestamp_to_iso(liked_at)

    return subsonic_album


def map_song(song_object: Dict | LibModel | Item, prefetched_sizes: Optional[Dict[str, int]] = None) -> Dict:

    data = standardise_datadict(song_object)

    beets_song_id = data.get('id', 0)
    song_id = beets_to_sub_song(beets_song_id)
    song_title = data.get('title') or ''

    subsonic_song = map_media(data)

    song_filepath = os.fsdecode(data.get('path', b''))
    album_id = beets_to_sub_album(data.get('album_id', 0))

    song_specific = {
        'id': song_id,
        'musicBrainzId': data.get('mb_releasetrackid') or data.get('mb_trackid') or '',
        'name': song_title,
        'sortName': song_title,
        'albumId': album_id,
        'coverArt': album_id or song_id,
        'language': data.get('language') or '',
        'path': song_filepath,
        'userRating': userdata_caching.one_rating(song_id),
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
        subsonic_song['replayGain'] = {
            'trackGain': round(float(tg or 0.0), 2),
            'albumGain': round(float(ag or 0.0), 2),
            'trackPeak': float(tp or 0.0),
            'albumPeak': float(ap or 0.0),
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

    stats = userdata_caching.one_play_stats(beets_song_id)
    if stats:
        subsonic_song['playCount'] = stats['play_count']
        if stats['last_played']:
            subsonic_song['played'] = timestamp_to_iso(stats['last_played'])

    liked_at = userdata_caching.one_like(subsonic_song['id'])
    if liked_at:
        subsonic_song['starred'] = timestamp_to_iso(liked_at)

    return subsonic_song


def map_artist(artist_name: str, with_albums: bool = True, prefetched: Optional[Dict] = None) -> Dict:

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

    meta = _artist_metadata(artist_name)
    mbid = mbid or meta['mbid']
    sort_name = sort_name if sort_name != artist_name else meta['sort_name']
    roles = meta['roles']

    if mbid:
        subsonic_artist_id = beets_to_sub_artist(mbid)
    else:
        subsonic_artist_id = beets_to_sub_artist(artist_name, is_mbid=False)

    subsonic_artist = {
        'id': subsonic_artist_id,
        'name': artist_name,
        'sortName': sort_name,
        'roles': roles,
        'musicBrainzId': mbid,
        'title': artist_name,
        'albumCount': album_count,
        'coverArt': subsonic_artist_id,
        'userRating': userdata_caching.one_rating(subsonic_artist_id),
        'artistImageUrl': imageart_url(subsonic_artist_id),
        'mediaType': 'artist'
    }

    if with_albums:

        if albums is None:  # already fetched above if not prefetched
            albums = list(flask.g.lib.albums(f'albumartist:{artist_name}'))

        userdata_caching.preload_albums(albums)
        song_counts = get_song_counts(albums)

        subsonic_artist['album'] = [
            map_album(alb, include_songs=False, song_counts=song_counts)
            for alb in albums
        ]

    liked_at = userdata_caching.one_like(subsonic_artist_id)
    if liked_at:
        subsonic_artist['starred'] = timestamp_to_iso(liked_at)

    return subsonic_artist


def map_playlist(playlist : 'Playlist', include_songs: bool = False) -> Dict:
    subsonic_playlist = {
        'id': playlist.id,
        'name': playlist.name,
        'songCount': playlist.song_count,
        'duration': playlist.duration,
        'created': timestamp_to_iso(playlist.ctime),
        'changed': timestamp_to_iso(playlist.mtime),

        # 'owner': 'userA',     # TODO
        # 'public': True,
    }
    if include_songs and playlist.songs:
        subsonic_playlist['entry'] = playlist.songs

    return subsonic_playlist


##
# Other more specialised utils


def _artist_metadata(name: str) -> Dict:
    """Lookup MBID, sort name and roles for a given artist name."""
    if not name:
        return {'mbid': '', 'sort_name': '', 'roles': []}

    cache = flask.g.setdefault('_artist_metadata_cache', {})
    if name in cache:
        return cache[name]

    mbid = ''
    sort_name = ''
    roles = []

    with flask.g.lib.transaction() as tx:
        album_rows = tx.query(
            """SELECT mb_albumartistid, albumartist_sort FROM albums WHERE albumartist = ? LIMIT 1""",
            (name,)
        )
        if album_rows:
            roles.append('albumartist')
            row = album_rows[0]
            if row[0]: mbid = row[0]
            if row[1]: sort_name = row[1]

        item_rows = tx.query(
            """SELECT mb_artistid, artist_sort FROM items WHERE artist = ? LIMIT 1""",
            (name,)
        )
        if item_rows:
            roles.append('artist')
            row = item_rows[0]
            if not mbid and row[0]: mbid = row[0]
            if not sort_name and row[1]: sort_name = row[1]

        # Check for secondary roles
        if not roles:
            if tx.query("""SELECT 1 FROM items WHERE artists LIKE ? LIMIT 1""", (f"%{name}%",)):
                roles.append('artist')

        if tx.query("""SELECT 1 FROM items WHERE composer = ? OR composer LIKE ? LIMIT 1""", (name, f"%{name}%")):
            roles.append('composer')

        if tx.query("""SELECT 1 FROM items WHERE lyricist = ? OR lyricist LIKE ? LIMIT 1""", (name, f"%{name}%")):
            roles.append('lyricist')

    result = {
        'mbid': mbid,
        'sort_name': sort_name or name,
        'roles': roles if roles else ['artist']
    }

    cache[name] = result
    return result


def resolve_artist(req_id: str) -> Tuple[str, str] | None:
    """
    Returns (name, mbid) for an artist from any subsonic ID (artist, album, or song)
    (or None if ID can't be resolved)
    """
    if req_id.startswith(SNG_ID_PREF):
        item = flask.g.lib.get_item(sub_to_beets_song(req_id))
        if not item:
            return None

        name = item.get('albumartist') or item.get('artist') or ''
        mbid = item.get('mb_albumartistid') or item.get('mb_artistid') or ''
        if not mbid:
            mbids = split_beets_multi(item.get('mb_albumartistids') or item.get('mb_artistids') or '')
            mbid = mbids[0] if mbids else ''

        return name, mbid

    if req_id.startswith(ALB_ID_PREF):
        album = flask.g.lib.get_album(sub_to_beets_album(req_id))
        if not album:
            return None

        name = album.get('albumartist') or ''
        mbid = album.get('mb_albumartistid') or ''
        if not mbid:
            mbids = split_beets_multi(album.get('mb_albumartistids') or '')
            mbid = mbids[0] if mbids else ''

        return name, mbid

    if req_id.startswith(ART_ID_PREF):
        value, is_mbid = sub_to_beets_artist(req_id)
    else:
        value, is_mbid = req_id, False

    if is_mbid:
        with flask.g.lib.transaction() as tx:
            # Check albums first
            rows = tx.query(
                """
                SELECT albumartist
                FROM albums
                WHERE mb_albumartistid = ?
                LIMIT 1
                """, (value,)
            )
            if not rows:  # fallback to items table
                rows = tx.query(
                    """
                    SELECT artist
                    FROM items
                    WHERE mb_artistid = ?
                    LIMIT 1
                    """, (value,)
                )

        artist_name = rows[0][0] if rows else ''
        if not artist_name:
            return None

        return artist_name, value   # value is the mbid

    else:
        artist_name = value
        meta = _artist_metadata(artist_name)
        return artist_name, meta['mbid']


def get_song_counts(albums: List[Dict]) -> Dict:
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
                mbid = mbids[i]
            elif is_contributor:
                meta = _artist_metadata(name)
                mbid = meta['mbid']

            contributor_id = beets_to_sub_artist(mbid, True) if mbid else beets_to_sub_artist(name, False)

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

    _process(data.get('composer') or '', '', contributors_array, seen_contributors, True, 'composer')
    _process(data.get('lyricist') or '', '', contributors_array, seen_contributors, True, 'lyricist')
    _process(data.get('remixer') or '', '', contributors_array, seen_contributors, True, 'remixer')
    _process(data.get('arranger') or '', '', contributors_array, seen_contributors, True, 'arranger')

    display_composer = ", ".join(composers)

    return artists_array, album_artists_array, contributors_array, display_composer