from __future__ import annotations

import flask
from flask import Blueprint

subsonic_bp = Blueprint('subsonic', __name__, url_prefix='/rest')

from .responses import TooManyIDs, subsonic_error


@subsonic_bp.errorhandler(TooManyIDs)
def _too_many_ids(e: TooManyIDs) -> flask.Response:
    return subsonic_error(0, message=str(e), resp_fmt=flask.request.values.get('f', 'xml'))


from .routes import (
    albums,
    artists,
    bookmarks,
    chat,
    coverart,
    general,
    jukebox,
    likes,
    lyrics,
    playlists,
    playqueue,
    podcasts,
    ratings,
    scrobble,
    search,
    shares,
    songs,
    sonic,
    stream,
    users,
    radio
)