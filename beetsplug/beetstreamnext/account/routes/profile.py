from __future__ import annotations

import flask

from .. import account_bp, account_required

from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.core.security import rate_limiter
from beetsplug.beetstreamnext.core.tempstore import temporary_store
from beetsplug.beetstreamnext.core.users_crud import (
    get_userdata, update_user, webui_login, regenerate_api_key, set_user_avatar
)
from beetsplug.beetstreamnext.forms import AccountProfileForm, ChangePasswordForm, flash_form_errors
from beetsplug.beetstreamnext.core.images import save_uploaded_avatar, avatar_response
from beetsplug.beetstreamnext.schemas import USER_ROLES_SCHEMA, allowed_bitrates


@account_bp.route('/', methods=['GET'])
@account_required
def route_account() -> str:

    username = flask.g.account_user

    token = flask.session.pop('_api_key_token', None)
    new_api_key = temporary_store.claim(token)

    if new_api_key and new_api_key.get('username') != username:
        new_api_key = None

    user = get_userdata(username, fields=[
        'username', 'email', 'maxBitRate', 'avatarLastChanged', *[r[0] for r in USER_ROLES_SCHEMA]
    ])

    form = AccountProfileForm(formdata=None, data=user)
    form.maxBitRate.choices = allowed_bitrates(int(user.get('maxBitRate') or 0))

    return flask.render_template(
        'account.html',
        user=user,
        profile_form=form,
        password_form=ChangePasswordForm(formdata=None),
        new_api_key=new_api_key,
        podcast_discovery_enabled=flask.current_app.config['podcast_manager'].discovery_enabled,
        current_username=username,
    )


@account_bp.route('/profile', methods=['POST'])
@account_required
def route_update_profile() -> flask.Response:

    username = flask.g.account_user
    current_limit = int(get_userdata(username, fields=['maxBitRate']).get('maxBitRate') or 0)

    form = AccountProfileForm()
    form.maxBitRate.choices = allowed_bitrates(current_limit)

    if form.validate_on_submit():
        try:
            # Whitelist the data
            update_user(
                username,
                email=safe_str(form.email.data) if form.email.data else '',
                maxBitRate=form.maxBitRate.data,
            )
            flask.flash('Settings saved.', 'success')

        except ValueError as e:
            flask.flash(str(e), 'error')

        except Exception as e:
            bsn_logger.error(f"Unexpected error updating profile of '{username}': {e}")
            flask.flash('An unexpected error occurred.', 'error')
    else:
        flash_form_errors(form)

    return flask.redirect(flask.url_for('account.route_account'))


@account_bp.route('/password', methods=['POST'])
@account_required
def route_change_password() -> flask.Response:

    username = flask.g.account_user
    client_ip = flask.request.remote_addr or 'unknown'

    form = ChangePasswordForm()

    if form.validate_on_submit():
        ok, _ = webui_login(username, form.current_password.data)
        if not ok:
            rate_limiter.record(client_ip, username)
            flask.flash('Current password is incorrect.', 'error')
        else:
            try:
                update_user(username, password=form.password.data)
                flask.flash('Password changed.', 'success')
            except ValueError as e:
                flask.flash(str(e), 'error')
    else:
        flash_form_errors(form)

    return flask.redirect(flask.url_for('account.route_account'))


@account_bp.route('/apikey', methods=['POST'])
@account_required
def route_regenerate_api_key() -> flask.Response:

    username = flask.g.account_user

    try:
        raw_api_key = regenerate_api_key(username)
        flask.session['_api_key_token'] = temporary_store.put({'username': username, 'key': raw_api_key})
        flask.flash('API key regenerated. The old key no longer works.', 'success')

    except ValueError as e:
        flask.flash(str(e), 'error')

    return flask.redirect(flask.url_for('account.route_account'))


@account_bp.route('/avatar', methods=['POST'])
@account_required
def route_upload_avatar() -> flask.Response:

    error = save_uploaded_avatar(flask.g.account_user)
    if error:
        flask.flash(error, 'error')
    else:
        flask.flash('Avatar updated.', 'success')

    return flask.redirect(flask.url_for('account.route_account'))


@account_bp.route('/avatar/delete', methods=['POST'])
@account_required
def route_delete_avatar() -> flask.Response:
    set_user_avatar(flask.g.account_user, None)
    flask.flash('Avatar removed.', 'success')
    return flask.redirect(flask.url_for('account.route_account'))


@account_bp.route('/avatar', methods=['GET'])
@account_required
def route_serve_avatar() -> flask.Response:
    return avatar_response(flask.g.account_user)
