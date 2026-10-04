from __future__ import annotations

import flask

from beetsplug.beetstreamnext.utils.htmx import modal_error
from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.core.users_crud import create_user, delete_user, update_user, regenerate_api_key, get_userdata, load_all_users, set_user_avatar, session_stamp
from beetsplug.beetstreamnext.core.images import read_uploaded_image
from beetsplug.beetstreamnext.core.avatars import avatar_response
from beetsplug.beetstreamnext.forms import UserForm, EditUserForm, collect_form_data, form_error_messages
from beetsplug.beetstreamnext.schemas import PUBLIC_USER_FIELDS


def _api_key_modal(username: str, raw_api_key: str) -> str:
    modal = flask.render_template('partials/api_key_modal.html', new_api_key={'username': username, 'key': raw_api_key})
    return f'<div id="apiKeySlot" hx-swap-oob="innerHTML">{modal}</div>'


@admin_bp.route('/users/create', methods=['POST'])
@admin_required
def route_create_user() -> str | flask.Response:
    form = UserForm()

    if not form.validate_on_submit():
        return modal_error(' '.join(form_error_messages(form)), 'createUserResult')

    try:
        data = collect_form_data(form)
        is_admin = data.pop('adminRole', False)
        # username/password are passed positionally and csrf_token isn't a user field
        data.pop('username', None)
        data.pop('password', None)
        data.pop('csrf_token', None)
        raw_api_key = create_user(
            form.username.data,
            form.password.data,
            admin=is_admin,
            **data
        )
    except ValueError as e:
        return modal_error(str(e), 'createUserResult')

    except Exception as e:
        bsn_logger.error(f'Unexpected error creating user: {e}')
        return modal_error('An unexpected error occurred while creating the user.', 'createUserResult')

    return _users_partial(f"User '{form.username.data}' created successfully.") + \
        _api_key_modal(safe_str(form.username.data), raw_api_key)


@admin_bp.route('/users/edit/<username>', methods=['GET'])
@admin_required
def route_edit_user(username) -> str:
    """Pre-filled edit form for user edit, lazy-loaded."""

    user = get_userdata(username, fields=list(PUBLIC_USER_FIELDS) + ['avatarLastChanged'])
    if not user:
        flask.abort(404)

    return flask.render_template(
        'partials/edit_user_form.html',
        user=user,
        edit_form=EditUserForm(formdata=None, data=user),
    )


@admin_bp.route('/users/update/<username>', methods=['POST'])
@admin_required
def route_update_user(username) -> str | flask.Response:
    form = EditUserForm()

    if not form.validate_on_submit():
        return modal_error(' '.join(form_error_messages(form)), 'editUserResult')

    try:
        updates = collect_form_data(form)

        if username == flask.session.get('username') and not updates.get('adminRole'):
            return modal_error("You can't remove your own admin role.", 'editUserResult')

        if form.password.data:
            updates['password'] = form.password.data

        avatar = read_uploaded_image()

        update_user(username, **updates)

        if 'password' in updates and username == flask.session.get('username'):
            flask.session['pv'] = session_stamp(username)   # Keep session alive

        if avatar is not None:
            set_user_avatar(username, avatar)

        elif flask.request.form.get('remove_avatar'):
            set_user_avatar(username, None)

    except ValueError as e:
        return modal_error(str(e), 'editUserResult')

    except Exception as e:
        bsn_logger.error(f"Unexpected error updating user '{username}': {e}")
        return modal_error('An unexpected error occurred while updating the user.', 'editUserResult')

    return _users_partial(f"User '{username}' updated successfully.")


def _users_partial(message: str | None = None, ok: bool = True) -> str:

    return flask.render_template(
        'partials/users_table.html',
        users=load_all_users(fields=list(PUBLIC_USER_FIELDS) + ['avatarLastChanged']),
        current_username=flask.session.get('username'),
        message=message,
        ok=ok,
    )


@admin_bp.route('/users', methods=['GET'])
@admin_required
def route_users() -> str:
    return _users_partial()


@admin_bp.route('/users/delete/<username>', methods=['POST'])
@admin_required
def route_delete_user(username) -> str:

    if username == flask.session.get('username'):
        return _users_partial("You can't delete your own account.", ok=False)

    if delete_user(username):
        return _users_partial(f"User '{username}' deleted.")

    return _users_partial(f"User '{username}' not found.", ok=False)


@admin_bp.route('/users/apikey/<username>', methods=['POST'])
@admin_required
def route_regenerate_api_key(username) -> str | flask.Response:
    try:
        raw_api_key = regenerate_api_key(username)
    except ValueError as e:
        return modal_error(str(e), 'editUserResult')

    return _users_partial(f"API key for '{username}' regenerated. The old key no longer works.") + \
        _api_key_modal(username, raw_api_key)


@admin_bp.route('/users/<username>/avatar', methods=['GET'])
@admin_required
def route_serve_avatar(username: str) -> flask.Response:
    return avatar_response(username)
