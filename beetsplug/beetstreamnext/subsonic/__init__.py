from __future__ import annotations
from flask import Blueprint

subsonic_bp = Blueprint('subsonic', __name__, url_prefix='/rest')

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