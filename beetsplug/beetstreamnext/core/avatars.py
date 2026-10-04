from __future__ import annotations

import hashlib
import flask

from beetsplug.beetstreamnext.core.images import read_uploaded_image, sniff_image
from beetsplug.beetstreamnext.core.users_crud import set_user_avatar, get_user_avatar


def save_uploaded_avatar(username: str) -> str | None:
    """Stores the uploaded avatar for `username`. Returns an error message, or None on success."""
    try:
        blob = read_uploaded_image()
    except ValueError as e:
        return str(e)

    if blob is None:
        return 'No file provided.'
    if not set_user_avatar(username, blob):
        return f"User '{username}' not found."
    return None


def avatar_response(username: str) -> flask.Response:
    """Cacheable (ETag) avatar image response."""
    blob, last_changed = get_user_avatar(username)

    if not blob:
        flask.abort(404)

    etag = hashlib.sha256(blob).hexdigest()[:16]
    if flask.request.if_none_match and etag in flask.request.if_none_match:
        return flask.Response(status=304)

    resp = flask.Response(blob, mimetype=sniff_image(blob) or 'image/jpeg')
    resp.set_etag(etag)
    resp.cache_control.private = True
    resp.cache_control.max_age = 300

    if last_changed:
        resp.last_modified = last_changed

    return resp
