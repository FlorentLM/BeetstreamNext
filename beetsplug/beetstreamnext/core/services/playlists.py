from __future__ import annotations

import contextlib
import csv
import io
import os
import sqlite3
import tempfile
import threading
import time
import urllib.parse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import flask

from beetsplug.beetstreamnext.application import app, with_app_context
from beetsplug.beetstreamnext.constants import PLAYLIST_EXPORT_DELAY_SEC, PLAYLIST_SYNC_INTERVAL_SEC
from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.core.library.indexes import SongIndex
from beetsplug.beetstreamnext.core.library.resolve import Resolve
from beetsplug.beetstreamnext.core.media.images import fetch_playlist_images
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.utils.concurrency import Throttle
from beetsplug.beetstreamnext.utils.system import creation_date, path_hash, relative_to, safe_filename
from beetsplug.beetstreamnext.utils.text import split_list


IMPORT_FLAG = 'playlists_imported'


class PlaylistNotFound(LookupError):
    pass


class PlaylistExists(Exception):
    pass


class PlaylistConflict(Exception):
    pass


@dataclass(frozen=True)
class PlaylistTrack:

    song_id: str | None
    path: str | None = None
    label: str | None = None
    duration: int = 0

    @classmethod
    def from_song(cls, song: Any) -> PlaylistTrack:
        path = song.get('path')

        return cls(
            song_id=IDs.encode_song(song),
            path=relative_to(path, app.config['music_root']) if path else None,
            label=' - '.join(filter(None, (song.get('artist'), song.get('title')))) or None,
            duration=int(song.get('length') or 0),
        )


class PlaylistManager:

    def __init__(self):
        self._sync_lock = threading.Lock()
        self._sync_throttle = Throttle()

        self._export_lock = threading.Lock()
        self._export_timers: dict[int, threading.Timer] = {}

    @staticmethod
    def _bump_version(db: sqlite3.Connection, playlist_id: int, expected_version: int | None) -> None:

        row = db.execute(
            """
            SELECT version 
            FROM playlists 
            WHERE id = ?
            """, (playlist_id,)
        ).fetchone()

        if row is None:
            raise PlaylistNotFound(playlist_id)

        if expected_version is not None and row['version'] != expected_version:
            raise PlaylistConflict(f"Playlist is at version {row['version']}, not {expected_version}.")

        db.execute(
            """
            UPDATE playlists
            SET version = version + 1, changed = ?
            WHERE id = ?
            """, (time.time(), playlist_id)
        )

    @staticmethod
    def _track_ids(db: sqlite3.Connection, playlist_id: int) -> list[int]:

        rows = db.execute(
            """
            SELECT id
            FROM playlist_tracks
            WHERE playlist_id = ?
            ORDER BY position, id
            """, (playlist_id,)
        )
        return [r['id'] for r in rows]

    @staticmethod
    def _coerce(tracks: Iterable[PlaylistTrack | str]) -> list[PlaylistTrack]:
        """Resolve song IDs against the library (drops unknown IDs)."""

        tracks = list(tracks)
        wanted = [t for t in tracks if isinstance(t, str)]
        songs = Resolve.songs(wanted) if wanted else {}

        return [
            t if isinstance(t, PlaylistTrack) else PlaylistTrack.from_song(songs[t])
            for t in tracks
            if not isinstance(t, str) or t in songs
        ]

    @classmethod
    def _insert_tracks(cls, db: sqlite3.Connection, playlist_id: int, tracks: Iterable[PlaylistTrack | str]) -> list[int]:
        """Positions are set afterwards by _renumber."""

        ids = []
        for t in cls._coerce(tracks):
            cur = db.execute(
                """
                INSERT INTO playlist_tracks (playlist_id, position, song_id, path, label, duration)
                VALUES (?, -1, ?, ?, ?, ?)
                """, (playlist_id, t.song_id, t.path, t.label, t.duration)
            )
            ids.append(cur.lastrowid)
        return ids

    @staticmethod
    def _renumber(db: sqlite3.Connection, order: Sequence[int]) -> None:
        db.executemany(
            """
            UPDATE playlist_tracks 
            SET position = ? 
            WHERE id = ?
            """, [(position, track_id) for position, track_id in enumerate(order)]
        )

    @staticmethod
    def get(playlist_id: int) -> dict | None:

        with database() as db:
            row = db.execute(
                """
                SELECT p.*, COUNT(t.id) AS song_count, COALESCE(SUM(t.duration), 0) AS duration
                FROM playlists p
                LEFT JOIN playlist_tracks t ON t.playlist_id = p.id
                WHERE p.id = ?
                GROUP BY p.id
                """, (playlist_id,)
            ).fetchone()

            if row is None:
                return None

            tracks = db.execute(
                """
                SELECT id, position, song_id, path, label, duration
                FROM playlist_tracks
                WHERE playlist_id = ?
                ORDER BY position, id
                """, (playlist_id,)
            ).fetchall()

        return {**row, 'tracks': [dict(t) for t in tracks]}

    @staticmethod
    def list_visible(username: str, is_admin: bool = False) -> list[dict]:
        """User's playlists and public playlists (or all playlists if admin)."""

        with database() as db:
            rows = db.execute(
                """
                SELECT p.*, COUNT(t.id) AS song_count, COALESCE(SUM(t.duration), 0) AS duration
                FROM playlists p
                LEFT JOIN playlist_tracks t ON t.playlist_id = p.id
                WHERE ? OR p.public = 1 OR p.owner = ?
                GROUP BY p.id
                ORDER BY p.name COLLATE NOCASE, p.id
                """, (is_admin, username)
            ).fetchall()

        return [dict(r) for r in rows]

    def create(self,
               owner: str | None,
               name: str,
               tracks: Iterable[PlaylistTrack | str] = (),
               comment: str = '',
               public: bool = False,
               created: float | None = None,
               changed: float | None = None,
               export: bool = True,
               ) -> int:

        now = time.time()

        with database(immediate=True) as db:
            try:
                cur = db.execute(
                    """
                    INSERT INTO playlists (owner, name, comment, public, created, changed)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """, (owner, name, comment, public, created or now, changed or now)
                )
            except sqlite3.IntegrityError as e:
                if 'UNIQUE' in str(e):
                    raise PlaylistExists(f"Playlist '{name}' already exists.") from e
                raise

            self._renumber(db, self._insert_tracks(db, cur.lastrowid, tracks))

        if export:
            self._schedule_export(cur.lastrowid)

        return cur.lastrowid

    def update(self,
            playlist_id: int,
            name: str | None = None,
            comment: str | None = None,
            public: bool | None = None,
            expected_version: int | None = None,
        ) -> None:

        with database(immediate=True) as db:
            self._bump_version(db, playlist_id, expected_version)
            try:
                db.execute(
                    """
                    UPDATE playlists
                    SET name    = COALESCE(?, name),
                        comment = COALESCE(?, comment),
                        public  = COALESCE(?, public)
                    WHERE id = ?
                    """, (name, comment, public, playlist_id)
                )

            except sqlite3.IntegrityError as e:
                raise PlaylistExists(f"Playlist '{name}' already exists.") from e

        self._schedule_export(playlist_id)

    def add_tracks(self,
                   playlist_id: int,
                   tracks: Iterable[PlaylistTrack | str],
                   position: int | None = None,
                   expected_version: int | None = None,
                   ) -> list[int]:
        """Appends by default. Returns the new track ids."""

        with database(immediate=True) as db:

            self._bump_version(db, playlist_id, expected_version)

            order = self._track_ids(db, playlist_id)
            new_ids = self._insert_tracks(db, playlist_id, tracks)

            at = len(order) if position is None else max(0, min(position, len(order)))
            self._renumber(db, order[:at] + new_ids + order[at:])

        self._schedule_export(playlist_id)
        return new_ids

    def remove_tracks(self,
            playlist_id: int,
            track_ids: Iterable[int] = (),
            positions: Iterable[int] = (),
            expected_version: int | None = None,
        ) -> int:
        """Returns how many tracks were removed. Positions are 0-based (and as of pre-removal)."""

        with database(immediate=True) as db:

            self._bump_version(db, playlist_id, expected_version)
            order = self._track_ids(db, playlist_id)

            doomed = set(track_ids).intersection(order)
            doomed.update(order[p] for p in positions if 0 <= p < len(order))

            db.executemany(
                """
                DELETE FROM playlist_tracks 
                WHERE id = ?
                """, [(t,) for t in doomed]
            )
            self._renumber(db, [t for t in order if t not in doomed])

        self._schedule_export(playlist_id)
        return len(doomed)

    def replace_tracks(self,
            playlist_id: int,
            tracks: Iterable[PlaylistTrack | str],
            expected_version: int | None = None,
        ) -> list[int]:
        """Returns the new track ids."""

        with database(immediate=True) as db:
            self._bump_version(db, playlist_id, expected_version)
            db.execute(
                """
                DELETE FROM playlist_tracks 
                WHERE playlist_id = ?
                """, (playlist_id,)
            )

            new_ids = self._insert_tracks(db, playlist_id, tracks)
            self._renumber(db, new_ids)

        self._schedule_export(playlist_id)
        return new_ids

    def move_track(self,
            playlist_id: int,
            track_id: int,
            new_position: int,
            expected_version: int | None = None,
        ) -> None:

        with database(immediate=True) as db:
            self._bump_version(db, playlist_id, expected_version)

            order = self._track_ids(db, playlist_id)

            if track_id not in order:
                raise LookupError(f"Track {track_id} is not in playlist {playlist_id}.")

            order.remove(track_id)
            order.insert(max(0, min(new_position, len(order))), track_id)
            self._renumber(db, order)

        self._schedule_export(playlist_id)

    def delete(self, playlist_id: int) -> None:
        """Also removes the playlist's m3u file."""

        with database(immediate=True) as db:
            row = db.execute(
                """
                SELECT source, exported
                FROM playlists
                WHERE id = ?
                """, (playlist_id,)
            ).fetchone()

            if row is None:
                raise PlaylistNotFound(playlist_id)

            db.execute(
                """
                DELETE FROM playlists
                WHERE id = ?
                """, (playlist_id,)
            )

        with self._export_lock:
            timer = self._export_timers.pop(playlist_id, None)
        if timer:
            timer.cancel()

        root = (app.config.get('playlist_dirs') or {}).get(0)
        file = Path(row['source']) if row['source'] else (Path(root) / row['exported'] if root and row['exported'] else None)
        if file:
            with contextlib.suppress(OSError):
                file.unlink()

    def _schedule_export(self, playlist_id: int) -> None:

        with self._export_lock:
            if previous := self._export_timers.pop(playlist_id, None):
                previous.cancel()

            timer = threading.Timer(PLAYLIST_EXPORT_DELAY_SEC, self._export, (playlist_id,))
            timer.daemon = True
            self._export_timers[playlist_id] = timer
            timer.start()

    def flush_exports(self) -> None:
        """Writes the pending m3u files right now."""

        with self._export_lock:
            pending = list(self._export_timers)
            for timer in self._export_timers.values():
                timer.cancel()

        for playlist_id in pending:
            self._export(playlist_id)

    @staticmethod
    def m3u_text(playlist: dict) -> str:
        """The playlist as extended m3u text."""

        lines = ['#EXTM3U']
        if playlist['comment']:
            lines.append(f"#PLAYLIST-DESC:{' '.join(playlist['comment'].split())}")
        if playlist['owner']:
            lines.append(f"#PLAYLIST-CREATOR:{playlist['owner']}")

        for t in playlist['tracks']:
            if t['path']:
                props = f" id={t['song_id']}" if t['song_id'] else ''
                lines.append(f"#EXTINF:{t['duration']}{props},{' '.join((t['label'] or '').split())}")
                lines.append(t['path'])

        return '\n'.join(lines) + '\n'

    @staticmethod
    def csv_text(playlist: dict) -> str:
        """The playlist as CSV text."""

        songs = Resolve.songs([t['song_id'] for t in playlist['tracks'] if t['song_id']])
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(['Track name', 'Artist', 'Album', 'Album artist', 'ISRC', 'Duration', 'MusicBrainz ID'])

        for track in playlist['tracks']:
            song = songs.get(track['song_id']) if track['song_id'] else None
            if song:
                writer.writerow([
                    song.get('title'), song.get('artist'), song.get('album'), song.get('albumartist'),
                    next(iter(split_list(song.get('isrc'), ';')), ''),
                    round(song.get('length') or 0), song.get('mb_releasetrackid') or song.get('mb_trackid'),
                ])
            else:
                writer.writerow([track['label'] or os.path.basename(track['path'] or ''), '', '', '', '', track['duration'], ''])

        return out.getvalue()

    @with_app_context
    def _export(self, playlist_id: int) -> None:
        """
        Writes the playlist's m3u file: in the playlist directory (private playlists in their owner's
        subfolder) or, for a mirrored playlist, in place. A rename or a visibility change moves the file.
        """

        with self._export_lock:
            self._export_timers.pop(playlist_id, None)

        playlist = self.get(playlist_id)
        root = (app.config.get('playlist_dirs') or {}).get(0)

        if playlist is None or playlist['readonly'] or (not playlist['source'] and not root):
            return

        name = safe_filename(playlist['name'], fallback='playlist')

        if playlist['source']:
            current = Path(playlist['source'])
            target = current.with_name(f"{name}{current.suffix}")
            if target != current and target.exists():
                target = current
        else:
            folder = Path(root)
            if playlist['owner'] and not playlist['public']:
                folder /= safe_filename(playlist['owner'])

            current = Path(root) / playlist['exported'] if playlist['exported'] else None
            target = folder / f'{name}.m3u'
            if target != current and target.exists():
                target = folder / f"{name} ({playlist_id}).m3u"

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=target.parent)
            try:
                with os.fdopen(fd, 'w', encoding='UTF-8') as f:
                    f.write(self.m3u_text(playlist))
                os.replace(tmp_path, target)
            except OSError:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
                raise

            if current and current != target:
                current.unlink(missing_ok=True)

        except OSError as e:
            bsn_logger.error(f"Failed to write playlist file {target}: {e}")
            return

        with database() as db:
            if playlist['source']:
                db.execute(
                    """
                    UPDATE playlists
                    SET source = ?, source_mtime = ?
                    WHERE id = ?
                    """, (str(target), target.stat().st_mtime, playlist_id)
                )
            else:
                db.execute(
                    """
                    UPDATE playlists
                    SET exported = ?
                    WHERE id = ?
                    """, (target.relative_to(root).as_posix(), playlist_id)
                )

    @staticmethod
    def _parse_m3u(path: Path) -> tuple[str, str, list[dict]]:

        comment = creator = ''
        entries = []
        current = {}

        with path.open('r', encoding='UTF-8', errors='replace') as f:

            for line in (ln.strip() for ln in f):

                if line.startswith('#EXTINF:'):

                    head, _, info = line[8:].partition(',')
                    fields = head.split()

                    with contextlib.suppress(ValueError, IndexError):
                        current['duration'] = max(int(fields[0]), 0)

                    current['info'] = info.strip()

                    current['props'] = {
                        k.strip(): v.strip('"').strip()
                        for p in fields[1:] if '=' in p
                        for k, v in [p.split('=', 1)]
                    }

                elif line.startswith('#EXTALBUMARTURL:'):
                    current['albumarturl'] = line[len('#EXTALBUMARTURL:'):].strip()

                elif line.startswith('#PLAYLIST-DESC:'):
                    comment = line[len('#PLAYLIST-DESC:'):].strip()

                elif line.startswith('#PLAYLIST-CREATOR:'):
                    creator = line[len('#PLAYLIST-CREATOR:'):].strip()

                elif line and not line.startswith('#'):
                    current['uri'] = line
                    entries.append(current)
                    current = {}

        return comment, creator, entries

    @staticmethod
    def _resolve_tracks(entries: list[dict], playlist_path: Path) -> list[PlaylistTrack]:

        music_root = Path(os.fsdecode(app.config['music_root']))

        song_ids: dict[int, str] = {}
        candidates: dict[int, list[Path]] = {}

        for idx, e in enumerate(entries):
            embedded = (e.get('props') or {}).get('id', '')

            if embedded and not embedded.isdigit():
                song_ids[idx] = embedded
                continue

            if embedded:    # Legacy (beets row id)
                item = flask.g.lib.get_item(int(embedded))

                if item is not None:
                    song_ids[idx] = IDs.encode_song(item)
                    continue

            # Old files have paths relative to music root
            candidates[idx] = [
                base / u
                for u in {e['uri'], urllib.parse.unquote(e['uri'])}
                for base in (playlist_path.parent, music_root)
            ]

        if candidates:
            by_hash = SongIndex.get().by_hash

            for idx, paths in candidates.items():
                for p in paths:
                    beets_id = by_hash.get(path_hash(os.path.normpath(p), music_root))

                    if beets_id is not None and (item := flask.g.lib.get_item(beets_id)):
                        song_ids[idx] = IDs.encode_song(item)
                        break

        return [
            PlaylistTrack(
                song_id=song_ids.get(idx),
                path=relative_to(e['uri'], music_root) if Path(e['uri']).is_absolute() else e['uri'],
                label=e.get('info') or None,
                duration=e.get('duration', 0),
            )
            for idx, e in enumerate(entries)
        ]

    @with_app_context
    def import_files(self, root_dir: str | Path | None = None) -> int:
        """Once only."""

        with database() as db:

            r = db.execute(
                    """
                    SELECT 1 FROM db_metadata 
                    WHERE key = ?
                    """, (IMPORT_FLAG,)
            )
            if r.fetchone():
                return 0

            rows = db.execute("""SELECT username FROM users""")
            users = {r['username'] for r in rows}

        root_dir = root_dir or (app.config.get('playlist_dirs') or {}).get(0)

        sources: list[tuple[Path, str | None]] = []

        if root_dir and Path(root_dir).is_dir():
            root = Path(root_dir)
            sources = [(p, None) for p in sorted(root.glob('*.m3u*'))]

            for user_dir in sorted(p for p in root.iterdir() if p.is_dir()):
                sources.extend((p, user_dir.name) for p in sorted(user_dir.glob('*.m3u*')))

        taken = {(p['owner'], p['name'].lower()) for p in self.list_visible('', is_admin=True)}

        imported = 0
        for path, folder in sources:
            try:
                comment, creator, entries = self._parse_m3u(path)

                owner = folder or creator
                owner = owner if owner in users else None

                # Name can exist in several folders
                name, n = path.stem, 1
                while (owner, name.lower()) in taken:
                    n += 1
                    name = f"{path.stem} ({n})"
                taken.add((owner, name.lower()))

                self.create(
                    owner=owner,
                    name=name,
                    tracks=self._resolve_tracks(entries, path),
                    comment=comment,
                    public=folder is None,
                    created=creation_date(path),
                    changed=path.stat().st_mtime,
                    export=False,
                )

                imported += 1

            except Exception as e:
                bsn_logger.error(f"Could not import playlist {path}: {e}")

        with database() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO db_metadata (key, value) VALUES (?, '1')
                """, (IMPORT_FLAG,)
            )

        if imported:
            bsn_logger.info(f"Imported {imported} playlist(s) from m3u files.")
        return imported

    @with_app_context
    def sync_sources(self) -> None:
        """Public read-only mirror of beets 'playlist' and 'smartplaylist' directories."""

        if not self._sync_throttle.allow(PLAYLIST_SYNC_INTERVAL_SEC):
            return

        dirs = app.config.get('playlist_dirs') or {}

        with self._sync_lock:

            # Smartplaylist first (a folder used by both plugins contains smart playlists)
            files: dict[str, tuple[Path, bool]] = {}    # {source: (path, readonly)}
            claimed: set[Path] = set()

            for dir_id in (2, 1):
                directory = dirs.get(dir_id)
                if directory is None or not Path(directory).is_dir():
                    continue
                if Path(directory).resolve() not in claimed:
                    files.update({str(p): (p, dir_id == 2) for p in sorted(Path(directory).glob('*.m3u*'))})
                claimed.add(Path(directory).resolve())

            with database() as db:
                rows = db.execute(
                    """
                    SELECT id, source, source_mtime 
                    FROM playlists 
                    WHERE source IS NOT NULL
                    """
                )

                known = {r['source']: r for r in rows}
                taken = {(p['owner'], p['name'].lower()) for p in self.list_visible('', is_admin=True)}

            for source in known.keys() - files.keys():
                self.delete(known[source]['id'])

            for source, (path, readonly) in files.items():
                try:
                    mtime = path.stat().st_mtime
                    if source in known and known[source]['source_mtime'] == mtime:
                        continue

                    comment, _creator, entries = self._parse_m3u(path)
                    tracks = self._resolve_tracks(entries, path)

                    with database(immediate=True) as db:
                        if source in known:
                            playlist_id = known[source]['id']

                            db.execute(
                                """
                                UPDATE playlists
                                SET comment = ?, changed = ?, source_mtime = ?, readonly = ?, version = version + 1
                                WHERE id = ?
                                """, (comment, mtime, mtime, readonly, playlist_id)
                            )

                            db.execute(
                                """
                                DELETE FROM playlist_tracks 
                                WHERE playlist_id = ?
                                """, (playlist_id,)
                            )

                        else:
                            name = path.stem
                            n = 1

                            while (None, name.lower()) in taken:
                                n += 1
                                name = f"{path.stem} ({n})"

                            taken.add((None, name.lower()))

                            playlist_id = db.execute(
                                """
                                INSERT INTO playlists (owner, name, comment, public, created, changed, source, source_mtime, readonly)
                                VALUES (NULL, ?, ?, 1, ?, ?, ?, ?, ?)
                                """, (name, comment, creation_date(path) or mtime, mtime, source, mtime, readonly)
                            ).lastrowid

                        self._renumber(db, self._insert_tracks(db, playlist_id, tracks))

                    for e, track in zip(entries, tracks, strict=True):
                        if e.get('albumarturl') and track.song_id and (song := Resolve.song(track.song_id)):
                            fetch_playlist_images(song, e['albumarturl'])

                except Exception as e:
                    bsn_logger.error(f"Failed to sync playlist {path.name}: {e}")
