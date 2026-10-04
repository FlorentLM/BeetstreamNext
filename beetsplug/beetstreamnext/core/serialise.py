from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Tuple, Dict, List, Any, Sequence, Callable
import flask
from beets.library import LibModel, Item

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.core.beets_interaction import write_beets_field
from beetsplug.beetstreamnext.core.external import query_musicbrainz, query_discogs
from beetsplug.beetstreamnext.core.cache import (
    preload_songs, preload_albums, one_rating, one_like, one_play_stats, avg_rating, get_song_counts
)
from beetsplug.beetstreamnext.core.images import tokenised_image_url
from beetsplug.beetstreamnext.core.ids import IDs, TYPES_PLAYABLE
from beetsplug.beetstreamnext.core.resolve import Resolve, standardise_datadict, get_artist_metadata
from beetsplug.beetstreamnext.utils.text import split_beets_multi, validate_mbid
from beetsplug.beetstreamnext.utils.general import timestamp_to_iso, genres_formatter, external_url
from beetsplug.beetstreamnext.utils.system import get_mimetype

if TYPE_CHECKING:
    from beetsplug.beetstreamnext.core.playlists import Playlist


def _get_artists(data: dict) -> Tuple[List[Dict], List[Dict], List[Dict], str]:
    """
    Split a song/album's raw artist/composer/lyricist/etc. fields into Subsonic ID3 artist refs.
    """

    artists_array = []
    album_artists_array = []
    contributors_array = []
    composers = []

    seen_artists = set()
    seen_album_artists = set()
    seen_contributors = set()

    def _process(raw_names: str, raw_mbids: str, target_list: list, seen_set: set, is_contributor: bool = False,
                 role: str = ''):
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
                meta = get_artist_metadata(name)
                mbid = meta['mbid']

            contributor_id = IDs.encode_artist(mbid or name, is_mbid=bool(mbid))
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
    _process(data.get('albumartists') or '', data.get('mb_albumartistids') or '', album_artists_array,
             seen_album_artists)

    _process(data.get('composers') or data.get('composer') or '', '', contributors_array, seen_contributors, True,
             'composer')
    _process(data.get('lyricists') or data.get('lyricist') or '', '', contributors_array, seen_contributors, True,
             'lyricist')
    _process(data.get('remixers') or data.get('remixer') or '', '', contributors_array, seen_contributors, True,
             'remixer')
    _process(data.get('arrangers') or data.get('arranger') or '', '', contributors_array, seen_contributors, True,
             'arranger')

    display_composer = ", ".join(composers)

    return artists_array, album_artists_array, contributors_array, display_composer


def _exact_albums_by(artist_name: str) -> List[LibModel]:
    """
    Albums whose albumartist exactly matches
    (beets' field:value query is a substring match)
    """

    with flask.g.lib.transaction() as tx:
        rows = tx.query(
            """
            SELECT id 
            FROM albums 
            WHERE albumartist = ?
            """, (artist_name,)
        )

    return [a for a in (flask.g.lib.get_album(row[0]) for row in rows) if a]


##
# Serialise: turn a resolved Beets/db object into its serialised Subsonic response dict.


class Serialise:
    """
    Maps a Beets/db object (as returned by the resolver) to its serialised Subsonic response dict.
    """

    _method_map: Dict[str, Callable] = {}

    @classmethod
    def _common(cls, beets_object: Dict | LibModel) -> dict:
        """
        Build the tag-derived fields (artist credits, genre, dates...) shared by song
        and album responses. Internal helper, not a member of the entry-type hierarchy.
        """

        data = standardise_datadict(beets_object)

        track_artist_name = data.get('artist') or data.get('albumartist') or ''

        main_ar_name = data.get('albumartist') or data.get('artist') or ''
        main_ar_mbid = validate_mbid(data.get('mb_albumartistid')) or validate_mbid(data.get('mb_artistid'))
        main_ar_multi = data.get('albumartists') or data.get('artists') or ''

        if IDs.is_joint_credit(main_ar_multi, main_ar_name):
            artist_id = IDs.encode_artist(main_ar_name, joint_credit=True)
        else:
            artist_id = IDs.encode_artist(main_ar_mbid or main_ar_name, is_mbid=bool(main_ar_mbid))

        artists, album_artists, contributors, display_composer = _get_artists(data)

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
            'year': data.get('original_year') or data.get('year') or 0,
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

    @classmethod
    def artist(cls, artist_name: str, with_albums: bool = True, prefetched: Optional[Dict] = None) -> dict:
        """
        Map a beets artist name to its serialised Subsonic ArtistID3 response dict.
        """

        # Priority: prefetched -> album query (when with_albums) -> standalone db query
        mbid = ''
        sort_name = artist_name
        album_count = 0
        albums = None
        is_joint = False

        if prefetched and artist_name in prefetched:
            pf = prefetched[artist_name]
            mbid = pf.get('mbid') or ''
            sort_name = pf.get('sort_name') or artist_name
            album_count = pf.get('album_count', 0)
            is_joint = pf.get('is_joint', False)

        elif with_albums:
            albums = _exact_albums_by(artist_name)
            if albums:
                mbid = albums[0].get('mb_albumartistid', '') or ''
                sort_name = albums[0].get('albumartist_sort', '') or artist_name
                is_joint = IDs.is_joint_credit(albums[0].get('albumartists'), artist_name)
            album_count = len(albums) if albums else 0

        else:
            with flask.g.lib.transaction() as tx:
                rows = tx.query(
                    """
                    SELECT COUNT(*), mb_albumartistid, albumartist_sort, albumartists
                    FROM albums
                    WHERE albumartist = ?
                    GROUP BY albumartist
                    """, (artist_name,)
                )

            if rows:
                row = rows[0]
                album_count, mbid, sort_name = row[0], row[1] or '', row[2] or artist_name
                is_joint = IDs.is_joint_credit(row[3], artist_name)

        meta = get_artist_metadata(artist_name)
        mbid = validate_mbid(mbid) or meta['mbid']  # meta['mbid'] is already validated by _artist_metadata()
        sort_name = sort_name if sort_name != artist_name else meta['sort_name']
        roles = meta['roles']

        if is_joint:
            subsonic_artist_id = IDs.encode_artist(artist_name, joint_credit=True)
        else:
            subsonic_artist_id = IDs.encode_artist(mbid or artist_name, is_mbid=bool(mbid))

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
            'artistImageUrl': tokenised_image_url(subsonic_artist_id),
            'mediaType': 'artist'
        }

        if with_albums:

            if albums is None:  # already fetched above if not prefetched
                albums = _exact_albums_by(artist_name)

            preload_albums(albums)
            song_counts = get_song_counts(albums)

            subsonic_artist['album'] = [
                cls.album(alb, include_songs=False, song_counts=song_counts)
                for alb in albums
            ]

        liked_at = one_like(subsonic_artist_id)
        if liked_at:
            subsonic_artist['starred'] = timestamp_to_iso(liked_at)

        return subsonic_artist

    @classmethod
    def album(cls, album_object: Dict | LibModel, include_songs: bool = True,
              song_counts: Optional[Dict] = None) -> dict:
        """
        Map a beets album object to its serialised Subsonic AlbumID3 response dict.
        """

        data = standardise_datadict(album_object)

        beets_album_id = data.get('id', 0)
        album_name = data.get('album', '')
        subsonic_album_id = IDs.encode_album(
            beets_album_id,
            data.get('mb_albumid'),
            data.get('albumartist'),
            album_name
        )

        subsonic_album = cls._common(data)

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
            subsonic_album['version'] = version.title()
            if app.config.get('save_album_version'):
                write_beets_field('album', data['id'], 'version', version.title(), allow_flex=True)

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
                {'disc': d + 1, 'title': ' - '.join(filter(None, [data.get('album', None), f'Disc {d + 1}']))}
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
            subsonic_album['song'] = [cls.song(s, prefetched_sizes=song_filesizes) for s in songs]

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

    @classmethod
    def song(cls, song_object: Dict | LibModel | Item, prefetched_sizes: Optional[Dict[str, int]] = None) -> dict:
        """
        Map a beets song item to its serialised Subsonic Child response dict.
        """

        data = standardise_datadict(song_object)

        song_id = IDs.encode_song(data)
        song_title = data.get('title') or ''

        subsonic_song = cls._common(data)

        song_filepath = os.fsdecode(data.get('path', b''))
        album_id = IDs.encode_album(
            data.get('album_id', 0),
            data.get('mb_albumid'),
            data.get('albumartist'),
            data.get('album')
        )

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
        work_stripped = work.strip()
        if work_stripped in ('-', '–', '—'):
            work_stripped = ''

        if work_stripped:
            title_stripped = song_title.strip()
            title_base = title_stripped
            if title_base.endswith(')'):
                paren_start = title_base.rfind('(')
                if paren_start != -1:
                    title_base = title_base[:paren_start].strip()

            if work_stripped.casefold() not in (title_stripped.casefold(), title_base.casefold()):
                work_obj = {'name': work_stripped}
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

    @classmethod
    def playlist(cls, playlist: 'Playlist', include_songs: bool = False) -> dict:
        """
        Map a Playlist object to its serialised Subsonic PlaylistWithSongs response dict.
        """
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
            'coverArt': playlist.id,
        }
        if include_songs and playlist.songs:
            subsonic_playlist['entry'] = playlist.songs

        return subsonic_playlist

    @classmethod
    def radio(cls, row: dict) -> dict:
        """
        Map an internet radio station row to its serialised Subsonic InternetRadioStation dict.
        """
        station_id = IDs.encode_radio(row['id'])

        subsonic_radio_station = {
            'id': station_id,
            'name': row['name'],
            'streamUrl': row['stream_url'],
            'homePageUrl': row['homepage_url'] or '',
            'coverArt': station_id
        }
        return subsonic_radio_station

    @classmethod
    def podcast_channel(cls, row: dict, episodes: Optional[List[dict]] = None) -> dict:
        channel_id = IDs.encode_podcast_channel(row['id'])

        subsonic_channel = {
            'id': channel_id,
            'url': row['url'],
            'title': row.get('title') or row['url'],
            'description': row.get('description') or '',
            'coverArt': channel_id,
            'originalImageUrl': row.get('image_url') or '',
            'status': row.get('status') or 'new',
        }

        if row.get('error_message'):
            subsonic_channel['errorMessage'] = row['error_message']

        if episodes is not None:
            subsonic_channel['episode'] = [cls.podcast_episode(ep, row) for ep in episodes]

        return subsonic_channel

    @classmethod
    def podcast_episode(cls, row: dict, channel: Optional[dict] = None) -> dict:

        episode_id = IDs.encode_podcast_episode(row['id'])
        channel_id = IDs.encode_podcast_channel(row['channel_id'])

        title = row.get('title') or ''
        channel_title = (channel or {}).get('title') or (channel or {}).get('channel_title') or ''

        subsonic_episode = {
            'id': episode_id,
            'parent': channel_id,
            'channelId': channel_id,
            'title': title,
            'name': title,
            'description': row.get('description') or '',
            'status': row.get('status') or 'new',
            'coverArt': channel_id,
            'isDir': False,
            'isVideo': False,
            'type': 'podcast',
            'mediaType': 'podcast',
            'duration': round(row.get('duration') or 0),
            'size': row.get('file_size') or 0,
        }

        if row.get('publish_date'):
            published_iso = timestamp_to_iso(row['publish_date'])
            if published_iso:
                subsonic_episode['publishDate'] = published_iso
                subsonic_episode['created'] = published_iso

        if channel_title:
            subsonic_episode['album'] = channel_title
            subsonic_episode['artist'] = channel_title

        if row.get('error_message'):
            subsonic_episode['errorMessage'] = row['error_message']

        if row.get('status') == 'completed' and row.get('file_path'):
            suffix = Path(row['file_path']).suffix.lstrip('.').lower() or 'mp3'
            subsonic_episode['suffix'] = suffix
            subsonic_episode['contentType'] = get_mimetype(row['file_path'])
            subsonic_episode['streamId'] = episode_id

        return subsonic_episode

    @classmethod
    def any(cls, entry_type: Optional[str], obj: Optional[Any]) -> Optional[dict]:
        """
        Map any resolvable Subsonic object (song, album, radio, podcast channel, or
        episode) to its serialised response dict, given the (type, object) pair
        returned by Resolve.any().
        """
        if obj is None:
            return None
        serialiser = cls._method_map.get(entry_type)
        return serialiser(obj) if serialiser else None

    @classmethod
    def playable(cls, entry_id: str, pre_resolved: Optional[Dict[str, Tuple[str, Any]]] = None) -> Optional[dict]:
        """
        Map any playable Subsonic ID (song, radio station, or podcast episode) to its serialised entry dict.

        Args:
            - pre_resolved: An optional {id: (type, object)} map from a prior resolve_many() call.
            Falls back to a single-item resolve() when it's not given or doesn't have the id.
        """

        entry_type, obj = (pre_resolved or {}).get(entry_id) or Resolve.any(entry_id)
        if obj is None or entry_type not in TYPES_PLAYABLE:
            return None

        if entry_type == 'song':
            return cls.song(obj)

        if entry_type == 'radio':
            return cls.radio(dict(obj))

        if entry_type == 'podcast_episode':
            channel = Resolve.podcast_channel(IDs.encode_podcast_channel(obj['channel_id']))
            return cls.podcast_episode(obj, channel)

    @classmethod
    def playables(cls, entry_ids: Sequence[str]) -> List[dict]:
        """
        Batched serialisation of playables. Maps a list of playable
        Subsonic IDs to their serialised entry dicts, skipping unresolvable ones.
        """
        resolved = Resolve.multiple(entry_ids)
        return [item for entry_id in entry_ids if (item := cls.playable(entry_id, resolved)) is not None]

    @classmethod
    def shared_items(cls, row: dict, entries: Sequence[str]) -> dict:
        """
        Map a public share row and its entry IDs to a serialised Subsonic Share dict.
        """

        songs, albums = Resolve.shared_items(entries)
        share_url = external_url(flask.url_for('public.share_view', share_id=row['id']))

        subsonic_share = {
            'id': row['id'],
            'url': share_url,
            'description': row['description'] or '',
            'username': row['username'],
            'created': timestamp_to_iso(row['created']),
            'expires': timestamp_to_iso(row['expires']),
            'visitCount': row['visit_count'],
            'entry': [cls.song(s) for s in songs] + [cls.album(a, include_songs=False) for a in albums]
        }
        return subsonic_share


Serialise._method_map = {
    'song': Serialise.song,
    'album': Serialise.album,
    'radio': Serialise.radio,
    'podcast_channel': Serialise.podcast_channel,
    'podcast_episode': Serialise.podcast_episode,
}
