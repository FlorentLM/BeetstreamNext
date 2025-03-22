from beetsplug.beetstream.utils import genres_splitter, creation_date
from beetsplug.beetstream import app
import flask
from typing import Union, List
from pathlib import Path
from itertools import chain

PL_ID_PREF = 'plid-'


def parse_m3u(filepath):
    """ Parses a playlist (m3u, m3u8 or m3a) and yields its entries """

    with open(filepath, 'r', encoding='UTF-8') as f:
        curr_entry = {}

        for line in f:
            line = line.strip()

            if not line:
                continue

            if line.startswith('#EXTM3U'):
                continue

            if line.startswith('#EXTINF:'):
                left_part, info = line[8:].split(",", 1)
                duration_and_props = left_part.split()
                curr_entry['info'] = info.strip()
                curr_entry['runtime'] = int(duration_and_props[0].strip())
                curr_entry['props'] = {k.strip(): v.strip('"').strip()
                                         for k, v in (p.split('=', 1) for p in duration_and_props[1:])}
                continue

            # Add content from any additional m3u directives
            elif line.startswith('#PLAYLIST:'):
                curr_entry['name'] = line[10:].strip()
                continue

            elif line.startswith('#EXTGRP:'):
                curr_entry['group'] = line[8:].strip()
                continue

            elif line.startswith('#EXTALB:'):
                curr_entry['album'] = line[8:].strip()
                continue

            elif line.startswith('#EXTART:'):
                curr_entry['artist'] = line[8:].strip()
                continue

            elif line.startswith('#EXTGENRE:'):
                curr_entry['genres'] = genres_splitter(line[10:])
                continue

            elif line.startswith('#EXTM3A'):
                curr_entry['m3a'] = True
                continue

            elif line.startswith('#EXTBYT:'):
                curr_entry['size'] = int(line[8:].strip())
                continue

            elif line.startswith('#EXTBIN:'):
                # Skip the binary mp3 content
                continue

            elif line.startswith('#EXTALBUMARTURL:'):
                curr_entry['artpath'] = line[16:].strip()
                continue

            elif line.startswith('#EXT-X-'):
                # We ignore HLS M3U fields
                continue

            curr_entry['uri'] = line
            yield curr_entry
            curr_entry = {}


class Playlist:
    def __init__(self, path):
        self.id = f'{PL_ID_PREF}{path.parent.stem.lower()}-{path.name}'
        self.name = path.stem
        self.ctime = creation_date(path)
        self.mtime = path.stat().st_mtime
        self.path = path
        self.songs = []
        self.duration = 0
        for entry in parse_m3u(path):

            entry_path = (path.parent / Path(entry['uri'])).resolve()
            entry_id = entry.get('props', {}).get('id', None)

            if entry_id:
                song = [flask.g.lib.get_item(entry_id)]
            else:
                with flask.g.lib.transaction() as tx:
                    song = tx.query("SELECT * FROM items WHERE (path) LIKE (?) LIMIT 1", (entry_path.as_posix(),))

            if song:
                self.songs.append(dict(song[0]))
                self.duration += int(song[0]['length'] or 0)


class PlaylistProvider:
    def __init__(self):

        self.playlist_dirs = app.config.get('playlist_dirs', set())
        self._playlists = {}

        if len(self.playlist_dirs) == 0:
            app.logger.warning('No playlist directories could be found.')

        else:
            for path in chain.from_iterable(Path(d).glob('*.m3u*') for d in self.playlist_dirs):
                try:
                    self._load_playlist(path)
                except Exception as e:
                    app.logger.error(f"Failed to load playlist {path.name}: {e}")

            app.logger.debug(f"Loaded {len(self._playlists)} playlists.")

    def _load_playlist(self, filepath):
        """ Load playlist data from a file, or from cache if it exists """

        file_mtime = filepath.stat().st_mtime
        playlist_id = f'{PL_ID_PREF}{'-'.join(filepath.parts[-2:]).lower()}'

        # Get potential cached version
        playlist = self._playlists.get(playlist_id)

        # If the playlist is not found in cache, or if the cached version is outdated
        if not playlist or playlist.mtime < file_mtime:
            # Load new data from file
            playlist = Playlist(filepath)
            # And cache it
            self._playlists[playlist_id] = playlist

        return playlist

    def get(self, playlist_id: str) -> Union[Playlist, None]:
        """ Get a playlist by its id """
        folder, file = playlist_id.rsplit('-')[1:]
        filepath = next(dir_path / file for dir_path in self.playlist_dirs if dir_path.stem.lower() == folder)

        if filepath.is_file():
            return self._load_playlist(filepath)
        else:
            return None

    def getall(self) -> List[Playlist]:
        """ Get all playlists """
        return list(self._playlists.values())