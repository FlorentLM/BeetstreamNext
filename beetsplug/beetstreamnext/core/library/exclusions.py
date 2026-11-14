from __future__ import annotations

from collections.abc import Callable
from typing import Any
import flask

from beetsplug.beetstreamnext.core.config.store import settings_store
from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.core.library.indexes import AlbumIndex, ArtistIndex, SongIndex
from beetsplug.beetstreamnext.core.library.resolve import Resolve, standardise_datadict
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.utils.general import api_bool


class Exclusions:
    """
    An item is hidden when disliked or rated low. Starred or high rated items are exempt.
    Hidden parents of non-hidden items still show up.
    """

    def __init__(self) -> None:

        r = flask.request.values

        self._dislike_mode = 'off' if api_bool(r.get('includeDisliked')) else settings_store.get('hide_dislikes')
        self._rating_mode = settings_store.get('hide_low_ratings')

        self._dislikes: set[str] = set()
        self._exempt: set[str] = set()      # starred or rated high
        self._rescued: set[str] = set()
        self._low_rated: set[str] = set()

        if self._dislike_mode == 'off' and self._rating_mode == 'off':
            return

        with database() as db:

            username = flask.g.username

            if self._dislike_mode != 'off':
                rows = db.execute(
                    """
                    SELECT item_id 
                    FROM dislikes 
                    WHERE username = ?
                    """, (username,)
                )
                self._dislikes = {row[0] for row in rows}

            if self._rating_mode != 'off':
                rows = db.execute(
                    """
                    SELECT item_id FROM ratings
                    WHERE username = ? AND rating BETWEEN 1 AND ?
                    """, (username, settings_store.get('low_rating_cutoff'))
                )
                self._low_rated = {row[0] for row in rows}

            if not (self._dislikes or self._low_rated):
                return

            rows = db.execute(
                """
                SELECT item_id 
                FROM likes 
                WHERE username = ?
                """, (username,)
            )
            self._exempt = {row[0] for row in rows}

            if cutoff := settings_store.get('high_rating_cutoff'):
                rows = db.execute(
                    """
                    SELECT item_id 
                    FROM ratings 
                    WHERE username = ? AND rating >= ?
                    """, (username, cutoff)
                )
                self._exempt |= {row[0] for row in rows}

        self._songs, self._albums, self._artists = SongIndex.get(), AlbumIndex.get(), ArtistIndex.get()

        # Albums and artists of exempt songs, artists of exempt albums
        for beets_id in Resolve.song_ids(list(self._exempt)).values():
            self._rescue_album(self._songs.album_of.get(beets_id))

        for exempt_id in self._exempt:
            if IDs.decode_type(exempt_id) == 'album':
                self._rescue_album(self._albums.beets_id(exempt_id), album_too=False)

        # Reference stored with an older artist ID? still counts
        for ids in (self._dislikes, self._low_rated, self._exempt):
            for old_id in [i for i in ids if IDs.decode_type(i) == 'artist' and i not in self._artists.by_id]:
                if artist := self._artists.lookup(old_id):
                    ids.add(artist.id)

    def _rescue_album(self, beets_id: int | None, album_too: bool = True) -> None:

        if beets_id is None:
            return

        if album_too and (album_id := self._albums.subsonic_ids.get(beets_id)):
            self._rescued.add(album_id)

        if artist := self._artists.by_album.get(beets_id):
            self._rescued.add(artist.id)

    @staticmethod
    def _applies(mode: str, scope: str) -> bool:
        return mode == 'everywhere' or (mode == 'lists' and scope == 'lists')

    def active(self, scope: str) -> bool:
        """
        Whether anything would be hidden in this scope.
        """
        return bool(
            (self._dislikes and self._applies(self._dislike_mode, scope))
            or (self._low_rated and self._applies(self._rating_mode, scope))
        )

    def _flagged(self, scope: str, *item_ids: str) -> bool:
        """
        Whether any of these items is disliked or low rated.
        """
        return (
            (self._applies(self._dislike_mode, scope) and any(i in self._dislikes for i in item_ids))
            or (self._applies(self._rating_mode, scope) and any(i in self._low_rated for i in item_ids))
        )

    def song_hidden(self, song_id: str, album_id: str, artist_id: str, scope: str, *, inherit: bool = True) -> bool:
        """
        inherit=True: a disliked album/artist also hides the songs (False: only the song's state counts).
        """
        if song_id in self._exempt:
            return False
        return self._flagged(scope, song_id, *((album_id, artist_id) if inherit else ()))

    def album_hidden(self, album_id: str, artist_id: str, scope: str, *, inherit: bool = True) -> bool:
        """
        inherit=True: a disliked artist also hides the albums (False: only the album's state counts).
        """

        if album_id in self._exempt or album_id in self._rescued:
            return False
        return self._flagged(scope, album_id, *((artist_id,) if inherit else ()))

    def artist_hidden(self, artist_id: str, scope: str) -> bool:
        return artist_id not in self._exempt and artist_id not in self._rescued and self._flagged(scope, artist_id)

    # Beets items / album rows

    def songs(self, items: list, scope: str) -> list:
        """The beets items that are not hidden."""

        if not self.active(scope):
            return items

        kept = []

        for item in items:
            album = self._songs.album_of.get(item['id'])
            album_id = self._albums.subsonic_ids.get(album)
            artist = self._artists.by_album.get(album)

            if album_id and artist:
                artist_id = artist.id

            else:   # No album (or one without an album artist)
                data = standardise_datadict(item)
                album_id = IDs.encode_album(data.get('album_id', 0), data.get('mb_albumid'), data.get('albumartist'), data.get('album'))
                artist_id = self._artists.id_of(data)

            if not self.song_hidden(self._songs.id_of(item), album_id, artist_id, scope):
                kept.append(item)

        return kept

    def albums(self, rows: list, scope: str) -> list:
        """The beets albums that are not hidden."""

        if not self.active(scope):
            return rows

        kept = []

        for row in rows:
            album_id = self._albums.subsonic_ids.get(row['id'])
            artist = self._artists.by_album.get(row['id'])

            if album_id and artist:
                artist_id = artist.id

            else:   # No album artist
                data = standardise_datadict(row)
                album_id = IDs.encode_album(data.get('id', 0), data.get('mb_albumid'), data.get('albumartist'), data.get('album'))
                artist_id = self._artists.id_of(data)

            if not self.album_hidden(album_id, artist_id, scope):
                kept.append(row)

        return kept

    def page(self,
            fetch: Callable[[int, int], list],
            keep: Callable[[list, str], list],
            scope: str,
            offset: int,
            count: int
        ) -> list:
        """
        One page of visible rows. fetch(limit, offset) runs the query
        """
        if not self.active(scope):
            return fetch(count, offset)

        chunk = max(count, 50) * 2
        page: list = []
        to_skip = offset
        position = 0

        while len(page) < count:
            rows = fetch(chunk, position)
            position += len(rows)

            visible = keep(rows, scope)
            dropped = min(to_skip, len(visible))
            to_skip -= dropped
            page.extend(visible[dropped:])

            if len(rows) < chunk:
                break

        return page[:count]

    # Serialised entries (dicts with Subsonic ids)

    def song_entries(self, songs: list[dict[str, Any]], scope: str, *, inherit: bool = True) -> list[dict[str, Any]]:
        """
        inherit=False: album opened directly, so keeps all its songs (unless the album is hidden)
        """
        if not self.active(scope):
            return songs

        kept = [s for s in songs if not self.song_hidden(s['id'], s.get('albumId', ''), s.get('artistId', ''), scope, inherit=inherit)]

        if not inherit and songs:
            album_id, artist_id = songs[0].get('albumId', ''), songs[0].get('artistId', '')

            if album_id not in self._exempt and self._flagged(scope, album_id, artist_id):
                only_exempt = [s for s in kept if s['id'] in self._exempt]
                if only_exempt:
                    return only_exempt
        return kept

    def album_entries(self, albums: list[dict[str, Any]], scope: str, *, inherit: bool = True) -> list[dict[str, Any]]:
        """
        inherit=False: artist opened directly, so keeps all its albums (unless the artist is hidden)
        """
        if not self.active(scope):
            return albums

        kept = [a for a in albums if not self.album_hidden(a['id'], a.get('artistId', ''), scope, inherit=inherit)]

        if not inherit and albums:
            artist_id = albums[0].get('artistId', '')

            if artist_id not in self._exempt and self._flagged(scope, artist_id):
                only_rescued = [a for a in kept if a['id'] in self._exempt or a['id'] in self._rescued]
                if only_rescued:
                    return only_rescued
        return kept


def exclusions() -> Exclusions:
    """Exclusions instance built once per request."""

    if 'exclusions' not in flask.g:
        flask.g.exclusions = Exclusions()

    return flask.g.exclusions

