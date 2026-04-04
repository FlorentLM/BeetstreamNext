from typing import Optional, List
import flask
from beetsplug.beetstreamnext.db import database
import beetsplug.beetstreamnext.utils as utils


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
            rows = utils.chunked_query(
                db_obj=db,
                query_template=query,
                chunked_values=ids,
                base_params=[flask.g.username]
            )
        return dict(rows)

    _batch_cache('_likes', fetch, subsonic_ids)


def one_like(item_id: str) -> Optional[float]:
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
            rows = utils.chunked_query(
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

def batch_play_stats(beets_song_ids: list[int]):

    def fetch(ids):
        query = """
            SELECT song_id, play_count, last_played 
            FROM play_stats 
            WHERE username=? AND song_id IN ({q})
        """
        with database() as db:
            rows = utils.chunked_query(
                db_obj=db,
                query_template=query,
                chunked_values=ids,
                base_params=[flask.g.username]
            )

        return {
            row['song_id']: {'play_count': row['play_count'], 'last_played': row['last_played']}
            for row in rows
        }

    _batch_cache('_play_stats', fetch, beets_song_ids)


def one_play_stats(beets_song_id: int) -> Optional[dict]:
    cache = flask.g.setdefault('_play_stats', {})

    if beets_song_id not in cache:
        with database() as db:
            row = db.execute(
                """
                SELECT play_count, last_played 
                FROM play_stats 
                WHERE username=? AND song_id=?
                """, (flask.g.username, beets_song_id)
            ).fetchone()

        cache[beets_song_id] = {'play_count': row[0], 'last_played': row[1]} if row else _MISSING

    result = cache[beets_song_id]
    return None if result is _MISSING else result


##


def preload_songs(beets_items: list):
    if not beets_items:
        return
    beets_ids = [s['id'] for s in beets_items]
    sub_ids = [utils.beets_to_sub_song(i) for i in beets_ids]

    batch_likes(sub_ids)
    batch_ratings(sub_ids)
    batch_play_stats(beets_ids)


def preload_albums(beets_albums: list):
    if not beets_albums:
        return
    sub_ids = [utils.beets_to_sub_album(a['id']) for a in beets_albums]

    batch_likes(sub_ids)
    batch_ratings(sub_ids)


def preload_artists(artists_data):

    if not artists_data:
        return

    sub_ids = []
    if isinstance(artists_data, dict):
        for name, data in artists_data.items():
            mbid = data.get('mbid')
            if mbid:
                sub_ids.append(utils.beets_to_sub_artist(mbid, is_mbid=True))
            else:
                sub_ids.append(utils.beets_to_sub_artist(name, is_mbid=False))

    elif isinstance(artists_data, list):
        for item in artists_data:
            if isinstance(item, str):
                sub_ids.append(utils.beets_to_sub_artist(item, is_mbid=False))
            elif isinstance(item, dict) or hasattr(item, 'keys'):
                name = item.get('albumartist') or item.get('artist') or ''
                mbid = item.get('mb_albumartistid') or item.get('mb_artistid') or ''
                if mbid:
                    sub_ids.append(utils.beets_to_sub_artist(mbid, is_mbid=True))
                elif name:
                    sub_ids.append(utils.beets_to_sub_artist(name, is_mbid=False))

    if sub_ids:
        batch_likes(sub_ids)
        batch_ratings(sub_ids)