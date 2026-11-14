from __future__ import annotations

import re
import shlex
from collections import defaultdict
from collections.abc import Callable
from functools import cached_property
from typing import TYPE_CHECKING, Any

import flask
from beets import context
from beets.dbcore.query import AndQuery, DateQuery, FieldQuery, InQuery, InvalidQueryError, NumericQuery
from beets.library import Album, Item, parse_query_parts

from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.core.library.indexes import AlbumIndex, Artist, ArtistIndex, SongIndex
from beetsplug.beetstreamnext.core.library.resolve import Resolve, standardise_datadict
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.utils.general import api_bool
from beetsplug.beetstreamnext.utils.text import remove_accents

if TYPE_CHECKING:
    from beetsplug.beetstreamnext.core.library.exclusions import Exclusions


class BeetsQuery:
    """
    Advanced search using Beets' query DSL
    (BSN addition: `starred:`, `rating:`, `plays:`, `lastplayed:` done per-user)
    """

    prefixes = ('b:', 'beets:')

    customised_fields= {
        'starred': NumericQuery,
        'rating': NumericQuery,
        'plays': NumericQuery,
        'lastplayed': DateQuery,
    }
    default_vals = {'starred': 0, 'rating': 0, 'plays': 0}
    token_re = re.compile(rf"^([-^]?)({'|'.join(customised_fields)}):(.+)$")

    def __init__(self, search: str, exc: Exclusions) -> None:
        """Raises InvalidQueryError for an unparsable string or token value."""

        query = search.split(':', 1)[1].strip()
        try:
            parts = shlex.split(query)
        except ValueError as exc:
            raise InvalidQueryError(query, exc) from exc

        self._exc = exc
        self.beets_parts: list[str] = []
        self.tokens: list[tuple[bool, FieldQuery]] = []     # (negated, query)

        for part in parts:
            match = self.token_re.match(part)
            if not match:
                self.beets_parts.append(part)
                continue

            negate, field, pattern = match.groups()
            if field == 'starred':
                pattern = str(int(api_bool(pattern)))

            self.tokens.append((bool(negate), self.customised_fields[field](field, pattern)))

    @cached_property
    def songs(self) -> list[Item]:
        return self._exc.songs(self._fetch(flask.g.lib.items, Item, 'song'), scope='browse')

    @cached_property
    def albums(self) -> list[Album]:
        return self._exc.albums(self._fetch(flask.g.lib.albums, Album, 'album'), scope='browse')

    @cached_property
    def artists(self) -> list[Artist]:

        index = ArtistIndex.get()

        if self.tokens and not self.beets_parts:
            found = {a.id: a for a in index.by_id.values() if not self._exc.artist_hidden(a.id, scope='browse')}

        else:
            if self.tokens:
                albums = self._exc.albums(list(flask.g.lib.albums(self.beets_parts)), scope='browse')
                songs = self._exc.songs(list(flask.g.lib.items(self.beets_parts)), scope='browse')
            else:
                albums, songs = self.albums, self.songs

            found = {}
            for album in albums:
                if artist := index.lookup(index.id_of(standardise_datadict(album))):
                    found[artist.id] = artist

            for song in songs:
                name = song.get('artist') or ''
                multi_names = song.get('artists')

                joint = IDs.is_joint_credit(multi_names, name)
                credit = [index.by_id.get(IDs.artist_id(name, joint=True))] if joint else []

                pairs = IDs.credit_pairs(name, song.get('mb_artistid'), multi_names, song.get('mb_artistids'), joint=joint)

                for artist in (*credit, *(index.by_id.get(IDs.artist_id(n, m)) or index.find(n) for n, m in pairs)):
                    if artist:
                        found[artist.id] = artist

        artists = sorted(found.values(), key=lambda a: remove_accents(a.name).lower())
        if not self.tokens:
            return artists

        keep, drop = self._match('artist')
        return [a for a in artists if (keep is None or a.id in keep) and a.id not in drop]

    def _fetch(self, fetch: Callable, model: type, kind: str) -> list:
        if not self.tokens:
            return list(fetch(self.beets_parts))

        keep, drop = self._match(kind)
        if keep is not None and not keep:
            return []

        if keep is not None and len(keep) <= 30000:  # IDs passed to SQLite (bound parameters limit: 32766)

            with context.music_dir(flask.g.lib.directory):
                query, sort = parse_query_parts(self.beets_parts, model)

            results = fetch(AndQuery([query, InQuery('id', list(keep))]), sort)
        else:
            results = fetch(self.beets_parts)

        return [r for r in results if (keep is None or r.id in keep) and r.id not in drop]

    def _match(self, kind: str) -> tuple[set | None, set]:
        """
        Returns (IDs to keep, IDs to drop)
        """
        fields = {query.field_name for _negate, query in self.tokens}
        username = flask.g.username

        with database() as db:
            liked = [row[0] for row in db.execute(
                """
                SELECT item_id
                FROM likes
                WHERE username = ?
                """, (username,))] if 'starred' in fields else []

            ratings = dict(db.execute(
                """
                SELECT item_id, rating
                FROM ratings
                WHERE username = ?
                """, (username,))) if 'rating' in fields else {}

            plays = db.execute(
                """
                SELECT song_id, play_count, last_played
                FROM play_stats
                WHERE username = ?
                """, (username,)).fetchall() if fields & {'plays', 'lastplayed'} else []

        if kind == 'song':
            def resolve(ids: list[str]) -> dict[str, Any]:
                return Resolve.song_ids(ids)

        elif kind == 'album':
            def resolve(ids: list[str]) -> dict[str, Any]:
                index = AlbumIndex.get()
                found = {i: index.beets_id(i) for i in ids if IDs.decode_type(i) == 'album'}
                return {i: beets_id for i, beets_id in found.items() if beets_id is not None}

        else:
            def resolve(ids: list[str]) -> dict[str, Any]:
                index = ArtistIndex.get()     # lookup: also IDs stored before an artist ID change
                found = {i: index.lookup(i) for i in ids if IDs.decode_type(i) == 'artist'}
                return {i: artist.id for i, artist in found.items() if artist is not None}

        values: dict[Any, dict[str, Any]] = defaultdict(lambda: dict(self.default_vals))

        for key in resolve(liked).values():
            values[key]['starred'] = 1
        for item_id, key in resolve(list(ratings)).items():
            values[key]['rating'] = ratings[item_id]

        if plays:
            song_ids = Resolve.song_ids([row[0] for row in plays])
            album_of = SongIndex.get().album_of
            artist_of = ArtistIndex.get().by_album

            for song_id, count, last_played in plays:
                key = song_ids.get(song_id)

                if kind == 'album':
                    key = album_of.get(key)
                elif kind == 'artist':
                    artist = artist_of.get(album_of.get(key))
                    key = artist.id if artist else None
                if key is None:
                    continue

                entry = values[key]
                entry['plays'] += count or 0
                if last_played and last_played > entry.get('lastplayed', 0):
                    entry['lastplayed'] = last_played

        keep = None
        drop = set()
        for negate, query in self.tokens:
            matched = {key for key, entry in values.items() if query.match(entry)}

            if query.match(self.default_vals) != negate:
                drop |= matched if negate else values.keys() - matched
            else:
                wanted = values.keys() - matched if negate else matched
                keep = wanted if keep is None else keep & wanted

        return keep, drop
