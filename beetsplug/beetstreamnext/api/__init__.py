from flask import Blueprint

api_bp = Blueprint('api', __name__, url_prefix='/rest')

from .routes import (
    albums,
    artists,
    bookmarks,
    chat,
    coverart,
    general,
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