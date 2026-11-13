from __future__ import annotations
import flask

from .. import subsonic_bp

from beetsplug.beetstreamnext.constants import MAX_ITEM_ID_CHAR
from beetsplug.beetstreamnext.core.storage.connection import database, dual_database
from beetsplug.beetstreamnext.core.storage.userprefs_cache import preload_songs, preload_albums, preload_artists, get_song_counts, touch_change
from beetsplug.beetstreamnext.core.config.store import settings_store
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.blueprints.subsonic.responses import subsonic_response, subsonic_error, cap_ids
from beetsplug.beetstreamnext.core.library.ids import IDs
from beetsplug.beetstreamnext.core.library.resolve import Resolve
from beetsplug.beetstreamnext.core.library.serialise import Serialise
from beetsplug.beetstreamnext.core.library.beets.interaction import commit_likes


def item_exists(item_id: str) -> bool:
    """
    Whether a song / album / artist / radio / podcast id points at something real.
    """

    if not item_id or len(item_id) > MAX_ITEM_ID_CHAR:
        return False

    if IDs.decode_type(item_id) == 'artist':
        return Resolve.artist(item_id) is not None

    return Resolve.any(item_id)[1] is not None


def set_liked(username: str, item_id: str, liked: bool) -> None:

    with database() as db:

        touch_change(db, username, 'exclusions')

        if liked:
            db.execute("""DELETE FROM dislikes WHERE username = ? AND item_id = ?""", (username, item_id))
            db.execute(
                """
                INSERT INTO likes (username, item_id)
                VALUES (?, ?)
                ON CONFLICT (username, item_id)
                    DO UPDATE SET starred_at = unixepoch()
                """, (username, item_id)
            )
        else:
            db.execute(
                """
                DELETE
                FROM likes
                WHERE username = ?
                  AND item_id = ?
                """, (username, item_id)
            )

    if username and username == settings_store.get('ratings_writeback_user'):
        commit_likes(item_id, 'starred', int(liked))


# Spec: https://opensubsonic.netlify.app/docs/endpoints/star/
@subsonic_bp.route('/star', methods=['GET', 'POST'])
@subsonic_bp.route('/star.view', methods=['GET', 'POST'])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/unstar/
@subsonic_bp.route('/unstar', methods=['GET', 'POST'])
@subsonic_bp.route('/unstar.view', methods=['GET', 'POST'])
def endpoint_star_or_unstar() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    song_ids = r.getlist('id', type=safe_str)
    album_ids = r.getlist('albumId', type=safe_str)
    artist_ids = r.getlist('artistId', type=safe_str)

    liked = 'unstar' not in flask.request.path

    if not any([song_ids, album_ids, artist_ids]):
        return subsonic_error(10, resp_fmt=resp_fmt)

    username = flask.g.username

    to_like = cap_ids(song_ids + album_ids + artist_ids)
    if liked:       # unstarring is not checked so IDs of removed items can be cleaned up
        to_like = [id_ for id_ in to_like if item_exists(id_)]

    for id_ in to_like:
        set_liked(username, id_, liked)

    return subsonic_response({}, resp_fmt=resp_fmt)


def set_disliked(username: str, item_id: str, disliked: bool) -> None:
    unstarred = _write_disliked(username, item_id, disliked)
    if unstarred and username == settings_store.get('ratings_writeback_user'):
        commit_likes(item_id, 'starred', 0)


def _write_disliked(username: str, item_id: str, disliked: bool) -> bool:
    """
    Returns whether a star was removed.
    """
    with database() as db:

        touch_change(db, username, 'exclusions')

        if disliked:
            unstarred = db.execute(
                """DELETE FROM likes WHERE username = ? AND item_id = ?""", (username, item_id)
            ).rowcount > 0
            db.execute(
                """
                INSERT INTO dislikes (username, item_id)
                VALUES (?, ?)
                ON CONFLICT (username, item_id)
                    DO UPDATE SET disliked_at = unixepoch()
                """, (username, item_id)
            )
            return unstarred

        db.execute("""DELETE FROM dislikes WHERE username = ? AND item_id = ?""", (username, item_id))
        return False


# BSN extension ('bsnDislike'), mirrors star/unstar
@subsonic_bp.route('/dislike', methods=['GET', 'POST'])
@subsonic_bp.route('/dislike.view', methods=['GET', 'POST'])
@subsonic_bp.route('/undislike', methods=['GET', 'POST'])
@subsonic_bp.route('/undislike.view', methods=['GET', 'POST'])
def endpoint_dislike_or_undislike() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    ids = r.getlist('id', type=safe_str) + r.getlist('albumId', type=safe_str) + r.getlist('artistId', type=safe_str)

    disliked = 'undislike' not in flask.request.path

    if not ids:
        return subsonic_error(10, resp_fmt=resp_fmt)

    username = flask.g.username

    to_set = cap_ids(ids)
    if disliked:    # undisliking is not checked so IDs of removed items can be cleaned up
        to_set = [id_ for id_ in to_set if item_exists(id_)]

    for id_ in to_set:
        set_disliked(username, id_, disliked)

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getStarred/
@subsonic_bp.route('/getStarred', methods=['GET', 'POST'])
@subsonic_bp.route('/getStarred.view', methods=['GET', 'POST'])

# Spec: https://opensubsonic.netlify.app/docs/endpoints/getStarred2/
@subsonic_bp.route('/getStarred2', methods=['GET', 'POST'])
@subsonic_bp.route('/getStarred2.view', methods=['GET', 'POST'])
def endpoint_get_starred() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    username = flask.g.username

    with dual_database() as db:
        song_id_rows = db.execute(
            """
            SELECT item_id
            FROM likes
            WHERE username = ? AND item_id LIKE 'sg-%'
            ORDER BY starred_at DESC
            """, (username,)
        ).fetchall()

        album_id_rows = db.execute(
            """
            SELECT item_id
            FROM likes
            WHERE username = ? AND item_id LIKE 'al-%'
            ORDER BY starred_at DESC
            """, (username,)
        ).fetchall()

        artist_rows = db.execute(
            """
            SELECT item_id 
            FROM likes 
            WHERE username = ? AND item_id LIKE 'ar-%' 
            ORDER BY starred_at DESC
            """, (username,)
        ).fetchall()

    song_ids_ordered = [row['item_id'] for row in song_id_rows]
    resolved = Resolve.songs(song_ids_ordered)
    song_items = [resolved[sid] for sid in song_ids_ordered if sid in resolved]

    preload_songs(song_items)

    songs = [Serialise.song(item) for item in song_items]
    album_dicts = []
    for row in album_id_rows:
        album = Resolve.album(row['item_id'])       # current ids (mbid / hash) as well as legacy ones
        if album is not None:
            album_dicts.append(dict(album))

    preload_albums(album_dicts)

    song_counts = get_song_counts(album_dicts)
    albums = [Serialise.album(row, include_songs=False, song_counts=song_counts) for row in album_dicts]


    starred_artists = [artist for artist in (Resolve.artist(row[0]) for row in artist_rows) if artist is not None]
    starred_artists = list({artist.id: artist for artist in starred_artists}.values())

    preload_artists(starred_artists)

    artists = [Serialise.artist(artist, with_albums=False) for artist in starred_artists]

    tag = 'starred2' if 'getStarred2' in flask.request.path else 'starred'
    payload = {
        tag: {
            'song':   songs,
            'album':  albums,
            'artist': artists,
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)