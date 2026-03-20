from typing import Union, List, Any
import os
from pathlib import Path
import flask

from beetsplug.beetstreamnext.utils import PLY_ID_PREF, genres_formatter, creation_date, map_song, sub_to_beets_song
from beetsplug.beetstreamnext import app


class Playlist:

    def __init__(self, dir_id, path):

        self.path = Path(path)
        self.dir_id = dir_id
        self.id = f"{PLY_ID_PREF}{self.dir_id}-{self.path.name.lower()}"
        self.name = self.path.stem
        self.ctime = creation_date(self.path)
        self.mtime = self.path.stat().st_mtime
        self.songs = []
        self.duration = 0

    def load_songs(self):
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

            question_marks = ','.join('?' * len(beets_ids))
            id_query = f"SELECT * FROM items WHERE id IN ({question_marks})"

            with flask.g.lib.transaction() as tx:
                rows = tx.query(id_query, beets_ids)

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
                absolute_paths_bytes.append(str(full_path).encode('utf-8'))

            question_marks = ','.join('?' * len(absolute_paths_bytes))
            path_query = f"SELECT * FROM items WHERE path IN ({question_marks})"

            with flask.g.lib.transaction() as tx:
                rows = tx.query(path_query, absolute_paths_bytes)

            path_map = {row['path']: row for row in rows}
            for (idx, entry), path_bytes in zip(path_entries, absolute_paths_bytes):
                row = path_map.get(path_bytes)
                if row:
                    results[idx] = row

        # Rebuild original order
        self.songs = []
        self.duration = 0
        for idx in sorted(results):
            row = results[idx]
            self.songs.append(map_song(row))
            self.duration += int(row['length'] or 0)

    def rename(self, name=None):
        if name and name != self.name:
            name = str(name).rstrip('/').rsplit('.', 1)[0]
            new_path = self.path.with_name(name).with_suffix('.m3u')

            if new_path.exists():
                raise FileExistsError(f"A playlist named {new_path.name} already exists.")

            self.path.rename(new_path)
            self.path = new_path
            self.name = self.path.stem
            self.id = f"{PLY_ID_PREF}{self.dir_id}-{self.path.name.lower()}"
            self.mtime = self.path.stat().st_mtime

    def remove_songs(self, indices: List[Any]):
        # need descending order so that removing an item doesn't shift other indices
        indices = sorted([int(i) for i in indices], reverse=True)
        for index in indices:
            if 0 <= index < len(self.songs):
                self.songs.pop(index)
        self._calc_duration()
        self.to_m3u()

    def add_songs(self, beets_items):
        for item in beets_items:
            self.songs.append(map_song(item))
        self._calc_duration()
        self.to_m3u()

    def _calc_duration(self):
        self.duration = sum(int(s.get('duration', 0) or 0) for s in self.songs)

    @classmethod
    def from_songs(cls, name, songs):
        """
        Create a new playlist from a list of beets songs, write it to disk, and return Playlist instance.
        """
        instance = cls.__new__(cls)

        instance.name = name.rsplit(".", 1)[0]
        instance.path = Path(flask.g.playlist_provider.playlist_dirs[0]) / f'{instance.name}.m3u'

        if instance.path.is_file():
            err = f"Playlist {instance.name}.m3u already exists in BeetstreamNext's folder!"
            app.logger.warning(err)
            raise FileExistsError(err)

        instance.dir_id = 0
        instance.id = f'{PLY_ID_PREF}{instance.dir_id}-{instance.path.name}'
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
    def from_m3u(cls, filepath):
        """Parse a playlist (m3u, m3u8 or m3a) and yield its entries."""

        filepath = Path(filepath)

        with filepath.open('r', encoding='UTF-8') as f:
            curr_entry = {}

            for line in f:
                line = line.strip()

                if not line or line.startswith('#EXTM3U'):
                    continue

                if line.startswith('#EXTINF:'):
                    left_part, info = line[8:].split(",", 1)
                    duration_and_props = left_part.split()
                    curr_entry['info'] = info.strip()
                    curr_entry['runtime'] = int(duration_and_props[0].strip())
                    curr_entry['props'] = {k.strip(): v.strip('"').strip()
                                           for k, v in (p.split('=', 1) for p in duration_and_props[1:])}

                elif line.startswith('#PLAYLIST:'):
                    curr_entry['name'] = line[10:].strip()

                elif line.startswith('#EXTGRP:'):
                    curr_entry['group'] = line[8:].strip()

                elif line.startswith('#EXTALB:'):
                    curr_entry['album'] = line[8:].strip()

                elif line.startswith('#EXTART:'):
                    curr_entry['artist'] = line[8:].strip()

                elif line.startswith('#EXTGENRE:'):
                    curr_entry['genres'] = genres_formatter(line[10:])

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

    def to_m3u(self):

        content = ['#EXTM3U']

        for song in self.songs:
            path = song.get('path')

            if isinstance(path, bytes):
                path = path.decode('utf-8')
            if not path:
                continue

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

            content.append(Path(path).relative_to(app.config['root_directory']).as_posix())

        with open(self.path.with_suffix('.m3u'), 'w', encoding='UTF-8') as f:
            f.write('\n'.join(content))


class PlaylistProvider:
    def __init__(self):

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

    def _load_playlist(self, dir_id, filepath):
        """Load playlist data from a file, or return the cached version if still current."""

        file_mtime = filepath.stat().st_mtime
        playlist_id = f"{PLY_ID_PREF}{dir_id}-{filepath.name.lower()}"

        # check cache
        playlist = self._playlists.get(playlist_id)

        if not playlist or playlist.mtime < file_mtime:
            playlist = Playlist(dir_id, filepath)
            # cache it
            self.register(playlist)

        return playlist

    def get(self, playlist_id: str) -> Union[Playlist, None]:
        """Get a playlist by its id, reloading from disk if file changed."""

        if not playlist_id.startswith(PLY_ID_PREF):
            return None

        playlist_id = playlist_id.lower()

        # try cache first
        if playlist_id in self._playlists:
            playlist = self._playlists[playlist_id]

            if playlist.path.is_file():
                playlist.load_songs()
                return self._load_playlist(playlist.dir_id, playlist.path)

        dir_key, file_name = playlist_id.removeprefix(PLY_ID_PREF).split('-', 1)
        dir_id = int(dir_key)

        dir_path = self.playlist_dirs.get(dir_id)
        if not dir_path:
            return None

        filepath = Path(dir_path) / file_name

        if filepath.is_file():
            playlist = self._load_playlist(dir_id, filepath)
            playlist.load_songs()
            return playlist

        return None

    def getall(self) -> List[Playlist]:
        """Return all cached playlists."""
        for playlist in self._playlists.values():
            playlist.load_songs()
        return list(self._playlists.values())

    def register(self, playlist: Playlist) -> None:
        self._playlists[playlist.id] = playlist

    def deregister(self, playlist_id: str):
        self._playlists.pop(playlist_id, None)

    def delete(self, playlist_id: str) -> None:
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