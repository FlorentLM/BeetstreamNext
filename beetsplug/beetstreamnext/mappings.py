import os
from typing import Optional, Tuple, Union, Dict, List

import flask
from beets import library

from beetsplug.beetstreamnext import userdata_caching as userdata_caching, app
from beetsplug.beetstreamnext.utils import (
    get_mimetype, timestamp_to_iso,
    SNG_ID_PREF, sub_to_beets_song, beets_to_sub_song,
    ALB_ID_PREF, sub_to_beets_album, beets_to_sub_album,
    ART_ID_PREF, sub_to_beets_artist, beets_to_sub_artist,
    genres_formatter, split_beets_multi, chunked_query, imageart_url
)


##

def standardise_datadict(obj: Union[dict, library.LibModel, any]) -> dict:
    """Standardise input (Beets Item/Album or sqlite3.Row) into a dict."""
    if isinstance(obj, library.LibModel):
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


def map_media(beets_object: Union[Dict, library.LibModel]) -> Dict:

    data = standardise_datadict(beets_object)

    artist_name = data.get('albumartist') or data.get('artist') or ''
    artist_mbid = data.get('mb_albumartistid') or data.get('mb_artistid') or ''
    raw_genres = f"{data.get('genres') or ''};{data.get('genre') or ''}"
    formatted_genres = genres_formatter(raw_genres)

    main_genre = formatted_genres[0] if formatted_genres else ''
    genres_list = [{'name': g} for g in formatted_genres]

    if artist_mbid:
        artist_id = beets_to_sub_artist(artist_mbid)
    else:
        artist_id = beets_to_sub_artist(artist_name, is_mbid=False)

    subsonic_media = {
        'artist': artist_name,
        'artistId': artist_id,
        'displayArtist': artist_name,
        'displayAlbumArtist': artist_name,
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
    return subsonic_media


def map_album(album_object: Union[Dict, library.Album], include_songs: bool = True, song_counts: Optional[Dict] = None) -> Dict:

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
        # 'version': 'Deluxe Edition', # TODO: items table has 'media' that contains "Vinyl", "CD"< "Digital Media", etc
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
            count, duration = rows[0][:2] if rows else (0, 0)
            subsonic_album['songCount'] = count
            subsonic_album['duration'] = round(duration)


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


def map_song(song_object: Union[Dict, library.Item], prefetched_sizes: Optional[Dict[str, int]] = None) -> Dict:

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
        'isrc': data.get('isrc') or '',
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

    # TODO: lyricist, composer, etc

    track_nb = data.get('track')
    if track_nb:
        subsonic_song['track'] = track_nb

    # subsonic_song['replayGain'] = {
    #         'trackGain': (song.get('rg_track_gain') or 0) or ((song.get('r128_track_gain') or 107) - 107),
    #         'albumGain': (song.get('rg_album_gain') or 0) or ((song.get('r128_album_gain') or 107) - 107),
    #         'trackPeak': song.get('rg_track_peak', 0),
    #         'albumPeak': song.get('rg_album_peak', 0)
    # }

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
    albums = None

    if prefetched and artist_name in prefetched:
        mbid = prefetched[artist_name].get('mbid') or ''

    elif with_albums:
        albums = list(flask.g.lib.albums(f'albumartist:{artist_name}'))
        if albums:
            mbid = albums[0].get('mb_albumartistid', '') or ''

    else:
        with flask.g.lib.transaction() as tx:
            rows = tx.query(
                """
                SELECT COUNT(*), mb_albumartistid
                FROM albums
                WHERE albumartist = ?
                GROUP BY albumartist
                """, (artist_name,)
            )
        if rows:
            mbid = rows[0][1] or ''

    if mbid:
        subsonic_artist_id = beets_to_sub_artist(mbid)
    else:
        subsonic_artist_id = beets_to_sub_artist(artist_name, is_mbid=False)

    subsonic_artist = {
        'id': subsonic_artist_id,
        'name': artist_name,
        'sortName': artist_name,
        'title': artist_name,
        'coverArt': subsonic_artist_id,
        'userRating': userdata_caching.one_rating(subsonic_artist_id),
        'artistImageUrl': imageart_url(subsonic_artist_id),

        # "roles": [
        #     "artist",
        #     "albumartist",
        #     "composer"
        # ],

        # This is only needed when part of a Child response
        'mediaType': 'artist'
    }

    if with_albums:

        if albums is None:  # already fetched above if not prefetched
            albums = list(flask.g.lib.albums(f'albumartist:{artist_name}'))

            if albums and not mbid:
                mbid = albums[0].get('mb_albumartistid', '') or ''

        userdata_caching.preload_albums(albums)

        subsonic_artist['albumCount'] = len(albums)
        subsonic_artist['musicBrainzId'] = mbid

        song_counts = get_song_counts(albums)
        subsonic_artist['album'] = [map_album(alb, include_songs=False, song_counts=song_counts) for alb in albums]

    else:
        if prefetched and artist_name in prefetched:
            subsonic_artist['albumCount'] = prefetched[artist_name]['album_count']
            subsonic_artist['musicBrainzId'] = mbid
        else:
            if rows:
                subsonic_artist['albumCount'] = rows[0][0]
                subsonic_artist['musicBrainzId'] = mbid
            else:
                subsonic_artist['albumCount'] = 0

    liked_at = userdata_caching.one_like(subsonic_artist_id)
    if liked_at:
        subsonic_artist['starred'] = timestamp_to_iso(liked_at)

    return subsonic_artist


def map_playlist(playlist, include_songs=False):
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


def resolve_artist(req_id: str) -> Optional[Tuple[str, str]]:
    """
    Returns (name, mbid) for an artist, from any subsonic ID (artist, album, or song)
    (or None if the ID can't be resolved).
    """
    if req_id.startswith(SNG_ID_PREF):
        item = flask.g.lib.get_item(sub_to_beets_song(req_id))
        if not item:
            return None

        return item.get('albumartist', ''), item.get('mb_artistid', '')

    if req_id.startswith(ALB_ID_PREF):
        album = flask.g.lib.get_album(sub_to_beets_album(req_id))
        if not album:
            return None

        return album.get('albumartist', ''), album.get('mb_artistid', '')

    # Artist ID (or name as fallback)
    if req_id.startswith(ART_ID_PREF):
        value, is_mbid = sub_to_beets_artist(req_id)
    else:
        value, is_mbid = req_id, False

    if is_mbid:
        with flask.g.lib.transaction() as tx:
            rows = tx.query(
                """
                SELECT albumartist 
                FROM albums 
                WHERE mb_albumartistid = ? 
                LIMIT 1
                """, (value,)
            )
        artist_name = rows[0][0] if rows else ''
        if not artist_name:
            return None

        return artist_name, value   # value is the mbid

    else:
        artist_name = value
        with flask.g.lib.transaction() as tx:
            rows = tx.query(
                """
                SELECT mb_artistid 
                FROM items 
                WHERE albumartist LIKE ? 
                LIMIT 1
                """, (artist_name,)
            )
        if not rows:
            return None

        return artist_name, rows[0][0] or ''


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

