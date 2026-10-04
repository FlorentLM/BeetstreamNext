from __future__ import annotations

import flask

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.core.images import save_uploaded_avatar, avatar_response
from beetsplug.beetstreamnext.core.users_crud import set_user_avatar


@admin_bp.route('/users/<username>/avatar', methods=['POST'])
@admin_required
def route_upload_avatar(username: str) -> flask.Response:
    error = save_uploaded_avatar(username)
    if error:
        flask.flash(error, 'error')
    else:
        flask.flash(f"Avatar updated for '{username}'.", 'success')

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
    return avatar_response(username)
