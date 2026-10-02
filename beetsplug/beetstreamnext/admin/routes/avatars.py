from __future__ import annotations

import hashlib
import io
import flask

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.constants import MAX_AVATAR_BYTES, MAX_AVATAR_DIM
from beetsplug.beetstreamnext.core.images import sniff_image, resize_image, ImageTooLarge
from beetsplug.beetstreamnext.core.users_crud import set_user_avatar, get_user_avatar


def read_uploaded_avatar(field: str = 'avatar') -> io.BytesIO | None:
    """Reads, validates and resizes uploaded avatar. None if no file was given."""
    file = flask.request.files.get(field)
    if file is None or not file.filename:
        return None

    # Read with a hard cap
    data = file.read(MAX_AVATAR_BYTES + 1)
    if len(data) > MAX_AVATAR_BYTES:
        raise ValueError(f'File too large (max {MAX_AVATAR_BYTES // 1024} KB).')

    if sniff_image(data) is None:
        raise ValueError('Unsupported or corrupt image. Use JPEG, PNG or WebP.')

    try:
        return resize_image(data, size=MAX_AVATAR_DIM, crop=True)
    except (ImageTooLarge, OSError):
        raise ValueError('Unsupported, corrupt, or oversized image.')


@admin_bp.route('/users/<username>/avatar', methods=['POST'])
@admin_required
def route_upload_avatar(username: str) -> flask.Response:
    try:
        blob = read_uploaded_avatar()
    except ValueError as e:
        flask.flash(str(e), 'error')
        return flask.redirect(flask.url_for('admin.route_settings'))

    if blob is None:
        flask.flash("No file provided.", 'error')
    elif set_user_avatar(username, blob):
        flask.flash(f"Avatar updated for '{username}'.", 'success')
    else:
        flask.flash(f"User '{username}' not found.", 'error')

    return flask.redirect(flask.url_for('admin.route_settings'))


@admin_bp.route('/users/<username>/avatar/delete', methods=['POST'])
@admin_required
def route_delete_avatar(username: str) -> flask.Response:
    if set_user_avatar(username, None):
        flask.flash(f"Avatar removed for '{username}'.", 'success')
    else:
        flask.flash(f"User '{username}' not found.", 'error')

    return flask.redirect(flask.url_for('admin.route_settings'))


@admin_bp.route('/users/<username>/avatar', methods=['GET'])
@admin_required
def route_serve_avatar(username: str) -> flask.Response:
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
