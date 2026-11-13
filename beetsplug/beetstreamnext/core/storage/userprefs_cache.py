from __future__ import annotations

import time
import flask

from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.utils.db import chunked_query

_MISSING = object()   # sentinel for "not found" vs. "not yet queried"


def _batch_cache(cache_key: str, fetch_fn, ids: list):
    """Load missing a batch of missing IDs into g cache."""

    cache = flask.g.setdefault(cache_key, {})

    missing = [i for i in ids if i not in cache]
    if not missing:
        return

    rows = fetch_fn(missing)

    # All queried IDs are marked seen (even those not found)
    cache.update({i: _MISSING for i in missing})
    cache.update(rows)   # overwrites with real values if found


##
# Change times

def touch_change(db, username: str, kind: str) -> None:
    """
    Record timestamp of when user changed some setting.
    """
    db.execute(
        """
        INSERT INTO user_changes (username, kind, changed_at)
        VALUES (?, ?, ?)
        ON CONFLICT (username, kind) DO UPDATE SET changed_at = excluded.changed_at
        """, (username, kind, time.time())
    )


##
# Likes

def batch_likes(subsonic_ids: list[str]):

    def fetch(ids):
        query = """
            SELECT item_id, starred_at 
            FROM likes 
            WHERE username=? AND item_id IN ({q})
        """
        with database() as db:
            rows = chunked_query(
                db_obj=db,
                query_template=query,
                chunked_values=ids,
                base_params=[flask.g.username]
            )
        return dict(rows)

    _batch_cache('_likes', fetch, subsonic_ids)


def one_like(item_id: str) -> float | None:
    cache = flask.g.setdefault('_likes', {})

    if item_id not in cache:
        with database() as db:
            row = db.execute(
                """
                SELECT starred_at 
                FROM likes 
                WHERE username=? AND item_id=?
                """, (flask.g.username, item_id)
            ).fetchone()

        cache[item_id] = row[0] if row else _MISSING

    result = cache[item_id]
    return None if result is _MISSING else result


##
# Dislikes

def batch_dislikes(subsonic_ids: list[str]):

    def fetch(ids):
        query = """
            SELECT item_id, disliked_at
            FROM dislikes
            WHERE username=? AND item_id IN ({q})
        """
        with database() as db:
            rows = chunked_query(
                db_obj=db,
                query_template=query,
                chunked_values=ids,
                base_params=[flask.g.username]
            )
        return dict(rows)

    _batch_cache('_dislikes', fetch, subsonic_ids)


def one_dislike(item_id: str) -> float | None:
    cache = flask.g.setdefault('_dislikes', {})

    if item_id not in cache:
        with database() as db:
            row = db.execute(
                """
                SELECT disliked_at
                FROM dislikes
                WHERE username=? AND item_id=?
                """, (flask.g.username, item_id)
            ).fetchone()

        cache[item_id] = row[0] if row else _MISSING

    result = cache[item_id]
    return None if result is _MISSING else result


##
# Ratings

def batch_ratings(subsonic_ids: list[str]):

    def fetch(ids):
        query = """
            SELECT item_id, rating 
            FROM ratings 
            WHERE username=? AND item_id IN ({q})
        """
        with database() as db:
            rows = chunked_query(
                db_obj=db,
                query_template=query,
                chunked_values=ids,
                base_params=[flask.g.username]
            )
        return dict(rows)

    _batch_cache('_ratings', fetch, subsonic_ids)


def one_rating(item_id: str) -> int:
    cache = flask.g.setdefault('_ratings', {})

    if item_id not in cache:
        with database() as db:
            row = db.execute(
                """
                SELECT rating 
                FROM ratings 
                WHERE username=? AND item_id=?
                """, (flask.g.username, item_id)
            ).fetchone()

        cache[item_id] = row[0] if row else _MISSING

    result = cache[item_id]
    return 0 if result is _MISSING else result


def avg_rating(item_id: str) -> tuple[float, int]:
    """Average rating across all users for an item (and the count)."""
    with database() as db:
        row = db.execute(
            """
            SELECT AVG(rating), COUNT(*)
            FROM ratings
            WHERE item_id=?
            """, (item_id,)
        ).fetchone()

    if not row or not row[1]:
        return 0.0, 0

    return round(row[0], 2), row[1]


##
# Play stats

def batch_play_stats(song_ids: list[str]):

    def fetch(ids):
        query = """
            SELECT song_id, play_count, last_played
            FROM play_stats
            WHERE username=? AND song_id IN ({q})
        """
        with database() as db:
            rows = chunked_query(
                db_obj=db,
                query_template=query,
                chunked_values=ids,
                base_params=[flask.g.username]
            )

        return {
            row['song_id']: {'play_count': row['play_count'], 'last_played': row['last_played']}
            for row in rows
        }

    _batch_cache('_play_stats', fetch, song_ids)


def one_play_stats(song_id: str) -> dict | None:
    cache = flask.g.setdefault('_play_stats', {})

    if song_id not in cache:
        with database() as db:
            row = db.execute(
                """
                SELECT play_count, last_played
                FROM play_stats
                WHERE username=? AND song_id=?
                """, (flask.g.username, song_id)
            ).fetchone()

        cache[song_id] = {'play_count': row[0], 'last_played': row[1]} if row else _MISSING

    result = cache[song_id]
    return None if result is _MISSING else result


##


def preload_songs(beets_items: list):
    if not beets_items:
        return
    from beetsplug.beetstreamnext.core.library.ids import IDs
    from beetsplug.beetstreamnext.core.library.resolve import standardise_datadict
    sub_ids = [IDs.encode_song(standardise_datadict(s)) for s in beets_items]

    batch_likes(sub_ids)
    batch_dislikes(sub_ids)
    batch_ratings(sub_ids)
    batch_play_stats(sub_ids)


def preload_albums(beets_albums: list):
    if not beets_albums:
        return
    from beetsplug.beetstreamnext.core.library.ids import IDs
    from beetsplug.beetstreamnext.core.library.resolve import standardise_datadict
    albums = [standardise_datadict(a) for a in beets_albums]
    sub_ids = [
        IDs.encode_album(a.get('id', 0), a.get('mb_albumid'), a.get('albumartist'), a.get('album'))
        for a in albums
    ]

    batch_likes(sub_ids)
    batch_dislikes(sub_ids)
    batch_ratings(sub_ids)


def preload_artists(artists) -> None:
    """Likes, dislikes and ratings of these artists in one query each."""

    sub_ids = [artist.id for artist in artists]

    if sub_ids:
        batch_likes(sub_ids)
        batch_dislikes(sub_ids)
        batch_ratings(sub_ids)


def get_song_counts(albums: list[dict]) -> dict:
    """Get song counts for a list of albums in a single db query."""

    album_ids = [row['id'] for row in albums]

    if not album_ids:
        return {}

    with flask.g.lib.transaction() as tx:
        sql_query = ('SELECT album_id, COUNT(*) as count, CAST(SUM(length) AS INTEGER) as duration'  # noqa: S608 vars are constants
                     + ' FROM items WHERE album_id IN ({q}) GROUP BY album_id')
        count_rows = chunked_query(
            db_obj=tx,
            query_template=sql_query,
            chunked_values=album_ids
        )

    return {row['album_id']: (row['count'], row['duration'] or 0) for row in count_rows}