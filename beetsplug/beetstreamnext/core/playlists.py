import threading
import urllib.parse
from typing import TYPE_CHECKING, List, Optional, Generator
import os
from pathlib import Path
import flask
from beets.util import bytestring_path

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.cache import preload_songs
from beetsplug.beetstreamnext.utils.general import genres_formatter
from beetsplug.beetstreamnext.utils.system import creation_date
from beetsplug.beetstreamnext.utils.db import chunked_query
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.core.images import fetch_playlist_images
from beetsplug.beetstreamnext.api.serializers import map_song, IDMapper

if TYPE_CHECKING:
    from beets.library import Item


def _validate_owner(name: str) -> None:
    """Usernames (used as a filesystem path) must be safe."""
    if not name or os.path.basename(name) != name or name in ('.', '..'):
        raise ValueError('Invalid username for playlist storage.')


class Playlist:

    def __init__(self, dir_id, path: str | Path, owner: Optional[str] = None):
        self._lock = threading.RLock()
        self.path = Path(path)
        self.dir_id = dir_id
        self.owner = owner
        self.id = self.make_id(dir_id, self.path, owner)
        self.name = self.path.stem[:200]
        self.ctime = creation_date(self.path)
        self.mtime = self.path.stat().st_mtime
        self.songs = []
        self.duration = 0
        self.song_count = 0
        self.comment = ''
        self.creator = ''
        self._parse_metadata()

    @staticmethod
    def make_id(dir_id, path: Path, owner: Optional[str]) -> str:
        """
        Make playlist's ID from its directory group, path (and owner for per-user ones).
        """
        stem_suffix = f"{path.stem[:200].lower()}{path.suffix.lower()}"
        if owner:
            return f"{IDMapper.PLY_ID_PREF}{dir_id}-{owner}/{stem_suffix}"
        return f"{IDMapper.PLY_ID_PREF}{dir_id}-{stem_suffix}"

    def _parse_metadata(self) -> None:
        """Quickly parse M3U for duration, song count, and playlist comment/description."""
        with self._lock:
            if not self.path.exists():
                return
            try:
                with self.path.open('r', encoding='UTF-8') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith('#EXTINF:'):
                            try:
                                runtime = int(line[8:].split(',', 1)[0].split()[0].strip())
                                if runtime > 0:
                                    self.duration += runtime
                            except (ValueError, IndexError):
                                pass
                        elif line.startswith('#PLAYLIST-DESC:'):
                            self.comment = line[len('#PLAYLIST-DESC:'):].strip()
                        elif line.startswith('#PLAYLIST-CREATOR:'):
                            self.creator = line[len('#PLAYLIST-CREATOR:'):].strip()
                        elif line and not line.startswith('#'):
                            self.song_count += 1
            except OSError:
                pass

    def load_songs(self) -> None:
        """Resolve all songs in the M3U in a minimal number of DB queries."""

        entries = list(self.from_m3u(self.path))
        if not entries:
            return

        id_entries = [(i, e) for i, e in enumerate(entries) if e.get('props', {}).get('id')]
        path_entries = [(i, e) for i, e in enumerate(entries) if not e.get('props', {}).get('id')]

        results = {}    # keyed by original entry index to keep order
        unresolved_by_id = []   # id_entries whose id didn't resolve fall back to path

        # Resolve songs that have an id embedded in the m3u: either a legacy raw beets
        # row id (old m3u files, plain digits) or a stable id (mbid/hash)
        if id_entries:
            legacy_entries = [(i, e) for i, e in id_entries if e['props']['id'].isdigit()]
            stable_entries = [(i, e) for i, e in id_entries if not e['props']['id'].isdigit()]

            if legacy_entries:
                beets_ids = [int(e['props']['id']) for _, e in legacy_entries]

                with flask.g.lib.transaction() as tx:
                    sql_query = 'SELECT * FROM items WHERE id IN ({q})'
                    rows = chunked_query(db_obj=tx, query_template=sql_query, chunked_values=beets_ids)

                id_map = {row['id']: row for row in rows}

                for idx, entry in legacy_entries:
                    row = id_map.get(int(entry['props']['id']))
                    if row:
                        results[idx] = row
                    else:
                        unresolved_by_id.append((idx, entry))

            if stable_entries:
                resolved = IDMapper.resolve_songs_bulk([e['props']['id'] for _, e in stable_entries])

                for idx, entry in stable_entries:
                    row = resolved.get(entry['props']['id'])
                    if row:
                        results[idx] = row
                    else:
                        unresolved_by_id.append((idx, entry))

        # Resolve songs that only have a path (try the path first, then
        # fall back to the percent-decoded one if that didn't match, then any
        # id-entries whose embedded id no longer resolves).
        path_entries = path_entries + unresolved_by_id
        if path_entries:
            literal_paths_bytes = []
            decoded_paths_bytes = []
            for _, e in path_entries:
                uri = e['uri']
                full_path = (self.path.parent / uri).resolve()
                literal_paths_bytes.append(bytestring_path(str(full_path)))

                decoded_uri = urllib.parse.unquote(uri)
                if decoded_uri != uri:
                    decoded_full_path = (self.path.parent / decoded_uri).resolve()
                    decoded_paths_bytes.append(bytestring_path(str(decoded_full_path)))
                else:
                    decoded_paths_bytes.append(None)

            candidates = list({
                p for p in literal_paths_bytes + decoded_paths_bytes if p is not None
            })

            with flask.g.lib.transaction() as tx:
                sql_query = 'SELECT * FROM items WHERE path IN ({q})'
                rows = chunked_query(db_obj=tx, query_template=sql_query, chunked_values=candidates)

            path_map = {row['path']: row for row in rows}

            for (idx, entry), literal_bytes, decoded_bytes in zip(path_entries, literal_paths_bytes, decoded_paths_bytes):
                row = path_map.get(literal_bytes)
                if not row and decoded_bytes is not None:
                    row = path_map.get(decoded_bytes)
                if row:
                    results[idx] = row

        # Rebuild original order
        preload_songs(list(results.values()))

        self.songs = []
        self.duration = 0
        for idx in sorted(results):
            row = results[idx]
            self.songs.append(map_song(row))
            self.duration += int(row['length'] or 0)

            art_url = entries[idx].get('albumarturl')
            if art_url:
                fetch_playlist_images(row, art_url)

        self.song_count = len(self.songs)

    def rename(self, name : Optional[str] = None) -> None:
        with self._lock:
            if name and name[:200] != self.name:
                safe_name = os.path.basename(str(name)).rsplit('.', 1)[0]
                safe_name = safe_name[:200]

                base_dir = self.path.parent.resolve()
                new_path = (base_dir / f"{safe_name}.m3u").resolve()
                if not new_path.is_relative_to(base_dir):
                    raise ValueError("Invalid rename target.")

                if new_path.exists():
                    raise FileExistsError(f"A playlist file named {new_path.name} already exists.")

                self.path.rename(new_path)
                self.path = new_path
                self.name = safe_name[:200]
                self.id = self.make_id(self.dir_id, self.path, self.owner)
                self.mtime = self.path.stat().st_mtime

    def set_public(self, make_public: bool, requester: str) -> None:
        """
        Move BeetstreamNext-owned playlist between the shared dir root (public) and a
        per-user subfolder (private).
        """
        with self._lock:
            if self.dir_id != PlaylistProvider.BSN_DIR_ID:
                raise ValueError('Only BeetstreamNext playlists can be made public/private.')

            if make_public == (self.owner is None):
                return

            if make_public:
                new_dir = self.path.parent.parent
                new_owner = None
            else:
                _validate_owner(requester)
                new_dir = self.path.parent / requester
                new_dir.mkdir(parents=True, exist_ok=True)
                new_owner = requester

            new_dir = new_dir.resolve()
            new_path = (new_dir / self.path.name).resolve()
            if not new_path.is_relative_to(new_dir):
                raise ValueError('Invalid playlist location.')

            if new_path.exists():
                raise FileExistsError(f"A playlist file named {new_path.name} already exists at the destination.")

            self.path.rename(new_path)
            self.path = new_path
            self.owner = new_owner
            self.id = self.make_id(self.dir_id, self.path, self.owner)
            self.mtime = self.path.stat().st_mtime

    def set_comment(self, comment: str) -> None:
        with self._lock:
            self.comment = comment[:1024]
            self.to_m3u()
            self.mtime = self.path.stat().st_mtime

    def remove_songs(self, indices: List[int]) -> None:
        with self._lock:
            for i in sorted(indices, reverse=True):  # descending order so that removing an item doesn't shift other indices
                if 0 <= i < len(self.songs):
                    self.songs.pop(i)
            self._calc_duration()
            self.to_m3u()

    def add_songs(self, beets_items) -> None:
        with self._lock:
            for item in beets_items:
                self.songs.append(map_song(item))
            self._calc_duration()
            self.to_m3u()

    def _calc_duration(self) -> None:
        self.duration = sum(int(s.get('duration', 0) or 0) for s in self.songs)

    @classmethod
    def from_songs(cls, name: str, songs: List['Item']) -> Playlist:
        """
        Create a new playlist from a list of beets songs, write it to disk, and return Playlist instance.
        """
        instance = cls.__new__(cls)
        instance._lock = threading.RLock()

        owner = flask.g.username
        _validate_owner(owner)

        safe_name = os.path.basename(os.fsdecode(name)).rsplit('.', 1)[0][:200]
        root_dir = Path(os.fsdecode(flask.g.playlist_provider.playlist_dirs.get(0))).resolve()
        base_dir = root_dir / owner
        base_dir.mkdir(parents=True, exist_ok=True)
        path = (base_dir / f'{safe_name}.m3u').resolve()

        if not path.is_relative_to(base_dir):
            raise ValueError('Invalid playlist name.')

        if path.is_file():
            err = f'Playlist {path.name} already exists!'
            bsn_logger.warning(err)
            raise FileExistsError(err)

        instance.name = safe_name
        instance.path = path

        instance.dir_id = 0
        instance.owner = owner
        instance.id = cls.make_id(instance.dir_id, instance.path, owner)
        instance.ctime = None
        instance.mtime = None
        instance.comment = ''
        instance.creator = owner
        instance.songs = [map_song(song) for song in songs]
        instance.song_count = len(instance.songs)
        instance.duration = sum(int(s.get('duration', 0) or 0) for s in instance.songs)

        # Save the new playlist
        instance.to_m3u()

        # Update timestamps
        instance.ctime = creation_date(instance.path)
        instance.mtime = instance.path.stat().st_mtime

        return instance

    @classmethod
    def from_m3u(cls, filepath) -> Generator:
        """Parse a playlist (m3u, m3u8 or m3a) and yield its entries."""

        filepath = Path(filepath)

        with filepath.open('r', encoding='UTF-8') as f:
            curr_entry = {}

            for line in f:
                line = line.strip()

                if not line or line.startswith('#EXTM3U'):
                    continue

                if line.startswith('#EXTINF:'):
                    try:
                        parts = line[8:].split(",", 1)
                        left_part = parts[0]
                        info = parts[1].strip() if len(parts) > 1 else ''
                        duration_and_props = left_part.split()
                        curr_entry['info'] = info
                        curr_entry['runtime'] = int(duration_and_props[0].strip())
                        curr_entry['props'] = {
                            k.strip(): v.strip('"').strip()
                            for p in duration_and_props[1:]
                            if '=' in p
                            for k, v in [p.split('=', 1)]
                        }
                    except (ValueError, IndexError):
                        pass

                elif line.startswith('#PLAYLIST:'):
                    curr_entry['name'] = line[10:].strip()

                elif line.startswith('#EXTGRP:'):
                    curr_entry['group'] = line[8:].strip()

                elif line.startswith('#EXTALB:'):
                    curr_entry['album'] = line[8:].strip()

                elif line.startswith('#EXTART:'):
                    curr_entry['artist'] = line[8:].strip()

                elif line.startswith('#EXTGENRE:'):
                    curr_entry['genres'] = list(genres_formatter(line[10:]))

                elif line.startswith('#EXTM3A'):
                    curr_entry['m3a'] = True

                elif line.startswith('#EXTBYT:'):
                    try:
                        curr_entry['size'] = int(line[8:].strip())
                    except ValueError:
                        pass

                elif line.startswith('#EXTALBUMARTURL:'):
                    curr_entry['albumarturl'] = line[len('#EXTALBUMARTURL:'):].strip()

                elif line.startswith('#EXTBIN:') or line.startswith('#EXT-X-'):
                    pass  # skip binary content and HLS fields

                elif not line.startswith('#'):
                    curr_entry['uri'] = line
                    yield curr_entry
                    curr_entry = {}

    def to_m3u(self) -> None:
        with self._lock:
            content = ['#EXTM3U']

            if self.comment:
                content.append(f"#PLAYLIST-DESC:{' '.join(self.comment.splitlines())}")

            if self.creator:
                content.append(f"#PLAYLIST-CREATOR:{self.creator}")

            for song in self.songs:
                path = song.get('path')
                if not path:
                    continue
                path = os.fsdecode(path)

                song_id = song.get('id', '')
                length = song.get('duration') or song.get('length', 0)
                info = f"#EXTINF:{round(length)} id={song_id}"

                artist = song.get('artist', '')
                title = song.get('title', '')
                album = song.get('album', '')
                year = song.get('year', '')

                if artist and title:
                    info += f',{artist} - {title}'
                elif artist:
                    info += f',{artist}'
                elif title:
                    info += f',{title}'
                content.append(info)

                if album:
                    albuminfo = f'#EXTALB:{album}'
                    albuminfo += f' ({year})' if year else ''
                    content.append(albuminfo)

                try:
                    path_str = Path(path).relative_to(app.config['root_directory']).as_posix()
                except ValueError:
                    path_str = Path(path).as_posix()
                content.append(path_str)

            suffix = '.m3u8' if self.path.suffix == '.m3u8' else '.m3u'
            with open(self.path.with_suffix(suffix), 'w', encoding='UTF-8') as f:
                f.write('\n'.join(content))


class PlaylistProvider:

    BSN_DIR_ID = 0
    SMARTPLAYLIST_DIR_ID = 2

    def __init__(self):
        self._lock = threading.RLock()
        self.playlist_dirs = app.config.get('playlist_dirs', {})
        self._playlists = {}

        if not self.playlist_dirs or all(v is None for v in self.playlist_dirs.values()):
            bsn_logger.warning('No playlist directories could be found.')
        else:
            for dir_id, dir_path in self.playlist_dirs.items():
                if dir_path is None:
                    continue
                for path, owner in self._iter_dir_entries(dir_id, Path(dir_path)):
                    try:
                        self._load_playlist(dir_id, path, owner)
                    except Exception as e:
                        bsn_logger.error(f"Failed to load playlist {path.name}: {e}")

            bsn_logger.debug(f"Loaded {len(self._playlists)} playlists.")

    @classmethod
    def _iter_dir_entries(cls, dir_id, dir_path: Path):
        """
        Yield (path, owner) for every playlist file in a playlist group's directory.
        """
        if not dir_path.is_dir():
            return

        for path in dir_path.glob('*.m3u*'):
            yield path, None

        if dir_id == cls.BSN_DIR_ID:
            for user_dir in dir_path.iterdir():
                if user_dir.is_dir():
                    for path in user_dir.glob('*.m3u*'):
                        yield path, user_dir.name

    def _load_playlist(self, dir_id, filepath, owner: Optional[str] = None) -> Playlist:
        """Load playlist data from a file, or return the cached version if still current."""

        file_mtime = filepath.stat().st_mtime
        playlist_id = Playlist.make_id(dir_id, filepath, owner)

        # check cache
        playlist = self._playlists.get(playlist_id)

        if not playlist or playlist.mtime < file_mtime:
            playlist = Playlist(dir_id, filepath, owner)
            # cache it
            self.register(playlist)

        return playlist

    def get(self, playlist_id: str) -> Playlist | None:
        """Get a playlist by its id, reloading from disk if file changed."""

        with self._lock:
            if not playlist_id.startswith(IDMapper.PLY_ID_PREF):
                return None

            playlist_id = playlist_id.lower()

            # try cache first
            if playlist_id in self._playlists:
                playlist = self._playlists[playlist_id]

                if playlist.path.is_file():
                    loaded = self._load_playlist(playlist.dir_id, playlist.path, playlist.owner)
                    loaded.load_songs()
                    return loaded

            try:
                parts = playlist_id.removeprefix(IDMapper.PLY_ID_PREF).split('-', 1)
                if len(parts) < 2:
                    return None
                dir_id = int(parts[0])
                rest = parts[1]
            except ValueError:
                return None

            dir_path = self.playlist_dirs.get(dir_id)
            if not dir_path:
                return None

            # Only dir 0 (owned) ids carry an '<owner>/<filename>' segment. Ignoring it for
            # every other dir_id keeps a crafted id from making a public (dir 1/2) playlist
            # look privately "owned" by whatever name happened to follow the dash.
            if dir_id == self.BSN_DIR_ID and '/' in rest:
                owner, file_name = rest.split('/', 1)
                owner = os.path.basename(owner)
            else:
                owner, file_name = None, rest

            safe_file_name = os.path.basename(file_name)
            base_path = Path(dir_path).resolve()
            if owner:
                base_path = base_path / owner
            filepath = (base_path / safe_file_name).resolve()
            if not filepath.is_relative_to(base_path):
                return None

            if filepath.is_file() and filepath.suffix.lower() in ('.m3u', '.m3u8'):
                playlist = self._load_playlist(dir_id, filepath, owner)
                playlist.load_songs()
                return playlist

            return None

    def getall(self) -> List[Playlist]:
        """Return all playlists, rescanning directories for changes."""
        with self._lock:
            for dir_id, dir_path in self.playlist_dirs.items():
                if dir_path is None:
                    continue

                entries = list(self._iter_dir_entries(dir_id, Path(dir_path)))
                current_paths = {str(path.resolve()) for path, _ in entries}

                # Remove playlists whose files have been deleted
                stale = [
                    pid for pid, pl in self._playlists.items()
                    if pl.dir_id == dir_id and str(pl.path.resolve()) not in current_paths
                ]
                for pid in stale:
                    self._playlists.pop(pid)

                # Register new files and reload modified ones
                for path, owner in entries:
                    try:
                        self._load_playlist(dir_id, path, owner)
                    except Exception as e:
                        bsn_logger.error(f"Failed to load playlist {path.name}: {e}")

            return list(self._playlists.values())

    def register(self, playlist: Playlist) -> None:
        with self._lock:
            self._playlists[playlist.id] = playlist

    def deregister(self, playlist_id: str) -> None:
        with self._lock:
            self._playlists.pop(playlist_id, None)

    def delete(self, playlist_id: str) -> None:
        with self._lock:
            playlist = self._playlists.get(playlist_id)
            if not playlist:
                raise FileNotFoundError(f"Playlist '{playlist_id}' not found.")

            path = Path(playlist.path)
            try:
                os.remove(path)
            except FileNotFoundError:
                err = f"Playlist {path.name} does not exist in {path.parent}."
                bsn_logger.warning(err)
                raise FileNotFoundError(err)
            finally:
                self.deregister(playlist_id) # always remove from cache