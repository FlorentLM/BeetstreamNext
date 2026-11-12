from __future__ import annotations

from collections.abc import Callable
import flask

from beetsplug.beetstreamnext.application import with_app_context
from beetsplug.beetstreamnext.blueprints.shared import route_registrar
from beetsplug.beetstreamnext.blueprints.subsonic.routes.likes import item_exists, set_liked, set_disliked
from beetsplug.beetstreamnext.blueprints.subsonic.routes.ratings import set_rating
from beetsplug.beetstreamnext.core.accounts.users_crud import get_user_roles
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.utils.db import chunked_query

ACTIONS = {'star', 'unstar', 'dislike', 'undislike', 'rating'}


def song_states(username: str, song_ids: list[str]) -> dict[str, dict]:
    """
    Star / dislike / rating for one user.
    """
    states = {song_id: {'starred': False, 'disliked': False, 'rating': 0} for song_id in song_ids}

    queries = (
        ('starred', 'SELECT item_id, 1 FROM likes WHERE username = ? AND item_id IN ({q})'),
        ('disliked', 'SELECT item_id, 1 FROM dislikes WHERE username = ? AND item_id IN ({q})'),
        ('rating', 'SELECT item_id, rating FROM ratings WHERE username = ? AND item_id IN ({q})'),
    )
    with database() as db:
        for key, query in queries:
            for item_id, value in chunked_query(db, query, list(states), base_params=[username]):
                states[item_id][key] = bool(value) if key != 'rating' else value

    return states


def register_userprefs_routes(bp: flask.Blueprint, login_required: Callable) -> None:
    """
    Admin panel and account page expose the same routes, only the auth decorator differs.
    Star, dislike and rate songs as the logged-in user.
    """
    route = route_registrar(bp, login_required)

    @route('/userprefs/<song_id>', ['GET'])
    def userprefs(song_id: str) -> flask.Response:
        return flask.jsonify(song_states(flask.session['username'], [song_id])[song_id])

    @route('/userprefs/<song_id>', ['POST'])
    @with_app_context
    def set_userpref(song_id: str) -> flask.Response:

        username = flask.session['username']
        action = flask.request.form.get('action', '')
        rating = flask.request.form.get('rating', default=0, type=int)

        if action not in ACTIONS or not 0 <= rating <= 5:
            flask.abort(400)

        if (action in ('star', 'dislike') or (action == 'rating' and rating)) and not item_exists(song_id):
            flask.abort(404)

        if action == 'rating':
            roles = get_user_roles(username)
            if not (roles.get('commentRole') or roles.get('adminRole')):
                flask.abort(403)
            set_rating(username, song_id, rating)

        elif action in ('star', 'unstar'):
            set_liked(username, song_id, action == 'star')

        else:
            set_disliked(username, song_id, action == 'dislike')

        return flask.jsonify(song_states(username, [song_id])[song_id])
