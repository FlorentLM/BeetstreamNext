import threading
from typing import TYPE_CHECKING, List, Optional, Generator
import os
from pathlib import Path
import flask
from beets.util import bytestring_path

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.userdata_caching import preload_songs
from beetsplug.beetstreamnext.utils import (
    genres_formatter, creation_date, sub_to_beets_song, chunked_query
)
from beetsplug.beetstreamnext.constants import PLY_ID_PREF
from beetsplug.beetstreamnext.mappings import map_song

if TYPE_CHECKING:
    from beets.library import Item


class Playlist:

    def __init__(self, dir_id, path: str | Path):
        self._lock = threading.RLock()
        self.path = Path(path)
        self.dir_id = dir_id
        self.id = f"{PLY_ID_PREF}{self.dir_id}-{self.path.stem[:200].lower()}{self.path.suffix.lower()}"
        self.name = self.path.stem[:200]
        self.ctime = creation_date(self.path)
        self.mtime = self.path.stat().st_mtime
        self.songs = []
        self.duration = 0
        self.song_count = 0
        self._parse_metadata()

    def _parse_metadata(self) -> None:
        """Quickly parse M3U for duration and song count."""
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

        # Resolve songs that have a beets id embedded in the m3u
        if id_entries:
            beets_ids = [int(e['props']['id']) for _, e in id_entries]

            with flask.g.lib.transaction() as tx:
                sql_query = 'SELECT * FROM items WHERE id IN ({q})'
                rows = chunked_query(db_obj=tx, query_template=sql_query, chunked_values=beets_ids)

            id_map = {row['id']: row for row in rows}

            for idx, entry in id_entries:
                beets_id = int(entry['props']['id'])
                row = id_map.get(beets_id)
                if row:
                    results[idx] = row

        # Resolve songs that only have a path
        if path_entries:
            absolute_paths_bytes = []
            for _, e in path_entries:
                uri = e['uri']
                full_path = (self.path.parent / uri).resolve()
                absolute_paths_bytes.append(bytestring_path(str(full_path)))

            with flask.g.lib.transaction() as tx:
                sql_query = 'SELECT * FROM items WHERE path IN ({q})'
                rows = chunked_query(db_obj=tx, query_template=sql_query, chunked_values=absolute_paths_bytes)

            path_map = {row['path']: row for row in rows}

            for (idx, entry), path_bytes in zip(path_entries, absolute_paths_bytes):
                row = path_map.get(path_bytes)
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
                self.id = f"{PLY_ID_PREF}{self.dir_id}-{self.path.stem.lower()[:200]}{self.path.suffix.lower()}"
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

        safe_name = os.path.basename(os.fsdecode(name)).rsplit('.', 1)[0][:200]
        base_dir = Path(os.fsdecode(flask.g.playlist_provider.playlist_dirs.get(0))).resolve()
        path = (base_dir / f'{safe_name}.m3u').resolve()

        if not path.is_relative_to(base_dir):
            raise ValueError('Invalid playlist name.')

        if path.is_file():
            err = f'Playlist {path.name} already exists!'
            app.logger.warning(err)
            raise FileExistsError(err)

        instance.name = safe_name
        instance.path = path

        instance.dir_id = 0
        instance.id = f'{PLY_ID_PREF}{instance.dir_id}-{instance.path.stem.lower()[:200]}{instance.path.suffix.lower()}'
        instance.ctime = None
        instance.mtime = None
        instance.songs = [map_song(song) for song in songs]
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
                    curr_entry['size'] = int(line[8:].strip())

                elif line.startswith('#EXTBIN:') or line.startswith('#EXT-X-') or line.startswith('#EXTALBUMARTURL:'):
                    pass  # skip binary content, HLS fields, and album art URLs
                    # TODO - maybe would be good to grab the album art from the url?

                elif not line.startswith('#'):
                    curr_entry['uri'] = line
                    yield curr_entry
                    curr_entry = {}

    def to_m3u(self) -> None:
        with self._lock:
            content = ['#EXTM3U']

            for song in self.songs:
                path = song.get('path')
                if not path:
                    continue
                path = os.fsdecode(path)

                song_id = sub_to_beets_song(song.get('id', ''))
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

            with open(self.path.with_suffix('.m3u'), 'w', encoding='UTF-8') as f:
                f.write('\n'.join(content))


class PlaylistProvider:

    def __init__(self):
        self._lock = threading.RLock()
        self.playlist_dirs = app.config.get('playlist_dirs', {})
        self._playlists = {}

        if not self.playlist_dirs or all(v is None for v in self.playlist_dirs.values()):
            app.logger.warning('No playlist directories could be found.')
        else:
            for dir_id, dir_path in self.playlist_dirs.items():
                if dir_path is not None:
                    dir_path = Path(dir_path)
                    for path in dir_path.glob('*.m3u*'):
                        try:
                            self._load_playlist(dir_id, path)
                        except Exception as e:
                            app.logger.error(f"Failed to load playlist {path.name}: {e}")

            app.logger.debug(f"Loaded {len(self._playlists)} playlists.")

    def _load_playlist(self, dir_id, filepath) -> Playlist:
        """Load playlist data from a file, or return the cached version if still current."""

        file_mtime = filepath.stat().st_mtime
        playlist_id = f"{PLY_ID_PREF}{dir_id}-{filepath.stem.lower()[:200]}{filepath.suffix.lower()}"

        # check cache
        playlist = self._playlists.get(playlist_id)

        if not playlist or playlist.mtime < file_mtime:
            playlist = Playlist(dir_id, filepath)
            # cache it
            self.register(playlist)

        return playlist

    def get(self, playlist_id: str) -> Playlist | None:
        """Get a playlist by its id, reloading from disk if file changed."""

        with self._lock:
            if not playlist_id.startswith(PLY_ID_PREF):
                return None

            playlist_id = playlist_id.lower()

            # try cache first
            if playlist_id in self._playlists:
                playlist = self._playlists[playlist_id]

                if playlist.path.is_file():
                    loaded = self._load_playlist(playlist.dir_id, playlist.path)
                    loaded.load_songs()
                    return loaded

            try:
                parts = playlist_id.removeprefix(PLY_ID_PREF).split('-', 1)
                if len(parts) < 2:
                    return None
                dir_id = int(parts[0])
                file_name = parts[1]
            except ValueError:
                return None

            dir_path = self.playlist_dirs.get(dir_id)
            if not dir_path:
                return None

            safe_file_name = os.path.basename(file_name)
            base_path = Path(dir_path).resolve()
            filepath = (base_path / safe_file_name).resolve()
            if not filepath.is_relative_to(base_path):
                return None

            if filepath.is_file() and filepath.suffix.lower() in ('.m3u', '.m3u8'):
                playlist = self._load_playlist(dir_id, filepath)
                playlist.load_songs()
                return playlist

            return None

    def getall(self) -> List[Playlist]:
        """Return all playlists, rescanning directories for changes."""
        with self._lock:
            for dir_id, dir_path in self.playlist_dirs.items():
                if dir_path is None:
                    continue

                dir_path = Path(dir_path)
                current_files = {f.name.lower() for f in dir_path.glob('*.m3u*')}

                # Remove playlists whose files have been deleted
                stale = [
                    pid for pid, pl in self._playlists.items()
                    if pl.dir_id == dir_id and pl.path.name.lower() not in current_files
                ]
                for pid in stale:
                    self._playlists.pop(pid)

                # Register new files and reload modified ones
                for path in dir_path.glob('*.m3u*'):
                    try:
                        self._load_playlist(dir_id, path)
                    except Exception as e:
                        app.logger.error(f"Failed to load playlist {path.name}: {e}")

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
                app.logger.warning(err)
                raise FileNotFoundError(err)
            finally:
                self.deregister(playlist_id) # always remove from cache