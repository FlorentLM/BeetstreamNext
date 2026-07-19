import flask
from flask_wtf import FlaskForm

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.core.tempstore import temporary_store
from beetsplug.beetstreamnext.core.users_crud import create_user, delete_user, update_user, regenerate_api_key
from beetsplug.beetstreamnext.admin.forms import UserForm, EditUserForm, collect_form_data


# Helpers

def _flash_multiple_errors(form: FlaskForm) -> None:
    for field_name, errors in form.errors.items():
        for error in errors:
            flask.flash(f'{field_name}: {error}', 'error')


@admin_bp.route('/users/create', methods=['POST'])
@admin_required
def route_create_user() -> flask.Response:
    form = UserForm()

    if form.validate_on_submit():
        try:
            data = collect_form_data(form)
            is_admin = data.pop('adminRole', False)
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
        _flash_multiple_errors(form)

    return flask.redirect(flask.url_for('admin.route_settings'))


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

            update_user(username, **updates)
            flask.flash(f"User '{username}' updated successfully.", 'success')

        except ValueError as e:
            flask.flash(str(e), 'error')

        except Exception as e:
            bsn_logger.error(f"Unexpected error updating user '{username}': {e}")
            flask.flash('An unexpected error occurred while updating the user.', 'error')
    else:
        _flash_multiple_errors(form)

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

