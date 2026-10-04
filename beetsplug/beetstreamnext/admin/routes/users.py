from __future__ import annotations

import flask

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.core.tempstore import temporary_store
from beetsplug.beetstreamnext.core.users_crud import create_user, delete_user, update_user, regenerate_api_key, get_userdata, set_user_avatar, session_stamp
from beetsplug.beetstreamnext.core.images import read_uploaded_image
from beetsplug.beetstreamnext.forms import UserForm, EditUserForm, collect_form_data, flash_form_errors
from beetsplug.beetstreamnext.schemas import PUBLIC_USER_FIELDS


@admin_bp.route('/users/create', methods=['POST'])
@admin_required
def route_create_user() -> flask.Response:
    form = UserForm()

    if form.validate_on_submit():
        try:
            data = collect_form_data(form)
            is_admin = data.pop('adminRole', False)
            # username/password are passed positionally and csrf_token isn't a user field
            # they are dropped them so they don't collide inside **data
            data.pop('username', None)
            data.pop('password', None)
            data.pop('csrf_token', None)
            raw_api_key = create_user(
                form.username.data,
                form.password.data,
                admin=is_admin,
                **data
            )

            token = temporary_store.put({'username': safe_str(form.username.data), 'key': raw_api_key})
            flask.session['_api_key_token'] = token

            flask.flash(f"User '{form.username.data}' created successfully.", 'success')

        except ValueError as e:
            flask.flash(str(e), 'error')

        except Exception as e:
            bsn_logger.error(f'Unexpected error creating user: {e}')
            flask.flash('An unexpected error occurred while creating the user.', 'error')
    else:
        flash_form_errors(form)

    return flask.redirect(flask.url_for('admin.route_settings'))


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
def route_update_user(username) -> flask.Response:
    form = EditUserForm()

    if form.validate_on_submit():
        try:
            updates = collect_form_data(form)

            if username == flask.session.get('username') and not updates.get('adminRole'):
                flask.flash("You can't remove your own admin role.", 'error')
                return flask.redirect(flask.url_for('admin.route_settings'))

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

            flask.flash(f"User '{username}' updated successfully.", 'success')

        except ValueError as e:
            flask.flash(str(e), 'error')

        except Exception as e:
            bsn_logger.error(f"Unexpected error updating user '{username}': {e}")
            flask.flash('An unexpected error occurred while updating the user.', 'error')
    else:
        flash_form_errors(form)

    return flask.redirect(flask.url_for('admin.route_settings'))


@admin_bp.route('/users/delete/<username>', methods=['POST'])
@admin_required
def route_delete_user(username) -> flask.Response:

    if username == flask.session.get('username'):
        flask.flash("You can't delete your own account.", 'error')
    elif delete_user(username):
        flask.flash(f"User '{username}' deleted.", 'info')
    else:
        flask.flash(f"User '{username}' not found.", 'info')

    return flask.redirect(flask.url_for('admin.route_settings'))


@admin_bp.route('/users/apikey/<username>', methods=['POST'])
@admin_required
def route_regenerate_api_key(username) -> flask.Response:
    try:
        raw_api_key = regenerate_api_key(username)

        token = temporary_store.put({'username': username, 'key': raw_api_key})
        flask.session['_api_key_token'] = token

        flask.flash(f"API key for '{username}' regenerated. The old key no longer works.", 'success')
    except ValueError as e:
        flask.flash(str(e), 'error')

    return flask.redirect(flask.url_for('admin.route_settings'))

