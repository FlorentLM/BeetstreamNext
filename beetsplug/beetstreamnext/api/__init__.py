from flask import Blueprint

api_bp = Blueprint('api', __name__, url_prefix='/rest')

from . import (
    albums,
    artists,
    bookmarks,
    coverart,
    general,
    likes,
    lyrics,
    playlists,
    playqueue,
    ratings,
    scrobble,
    search,
    songs,
    stream,
    users
)