from typing import List
import flask

from beetsplug.beetstreamnext.api.idmapper import IDMapper, standardise_datadict
from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.utils.db import chunked_query
from beetsplug.beetstreamnext.utils.text import validate_mbid

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
# Likes

def batch_likes(subsonic_ids: List[str]):

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
# Ratings

def batch_ratings(subsonic_ids: List[str]):

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


##
# Play stats

def batch_play_stats(song_ids: List[str]):

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
    sub_ids = [IDMapper.mint_song(standardise_datadict(s)) for s in beets_items]

    batch_likes(sub_ids)
    batch_ratings(sub_ids)
    batch_play_stats(sub_ids)


def preload_albums(beets_albums: list):
    if not beets_albums:
        return
    sub_ids = [IDMapper.mint_album(a['id']) for a in beets_albums]

    batch_likes(sub_ids)
    batch_ratings(sub_ids)


def preload_artists(artists_data):

    if not artists_data:
        return

    sub_ids = []
    if isinstance(artists_data, dict):
        for name, data in artists_data.items():
            mbid = validate_mbid(data.get('mbid'))
            sub_ids.append(IDMapper.mint_artist(mbid or name, is_mbid=bool(mbid)))

    elif isinstance(artists_data, list):
        for item in artists_data:
            if isinstance(item, str):
                sub_ids.append(IDMapper.mint_artist(item, is_mbid=False))

            elif isinstance(item, dict) or hasattr(item, 'keys'):
                name = item.get('albumartist') or item.get('artist') or ''
                mbid = validate_mbid(item.get('mb_albumartistid')) or validate_mbid(item.get('mb_artistid'))
                sub_ids.append(IDMapper.mint_artist(mbid or name, is_mbid=bool(mbid)))

    if sub_ids:
        batch_likes(sub_ids)
        batch_ratings(sub_ids)