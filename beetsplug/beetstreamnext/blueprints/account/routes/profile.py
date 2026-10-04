from __future__ import annotations

import flask

from .. import account_bp, account_required

from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.core.accounts.security import rate_limiter
from beetsplug.beetstreamnext.core.storage.tempstore import temporary_store
from beetsplug.beetstreamnext.core.accounts.users_crud import (
    get_userdata, update_user, session_stamp, regenerate_api_key, set_user_avatar
)
from beetsplug.beetstreamnext.core.accounts.credentials import webui_login
from beetsplug.beetstreamnext.blueprints.forms import AccountProfileForm, ChangePasswordForm, form_error_messages
from beetsplug.beetstreamnext.core.media.avatars import save_uploaded_avatar, avatar_response
from beetsplug.beetstreamnext.core.accounts.user_schema import USER_ROLES_SCHEMA, allowed_bitrates


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
def route_update_profile() -> str:

    username = flask.g.account_user
    current_limit = int(get_userdata(username, fields=['maxBitRate']).get('maxBitRate') or 0)

    form = AccountProfileForm()
    form.maxBitRate.choices = allowed_bitrates(current_limit)

    if not form.validate_on_submit():
        return flask.render_template('partials/action_result.html', message=' '.join(form_error_messages(form)), ok=False)

    try:
        # Whitelist the data
        update_user(
            username,
            email=safe_str(form.email.data) if form.email.data else '',
            maxBitRate=form.maxBitRate.data,
        )
    except ValueError as e:
        return flask.render_template('partials/action_result.html', message=str(e), ok=False)

    except Exception as e:
        bsn_logger.error(f"Unexpected error updating profile of '{username}': {e}")
        return flask.render_template('partials/action_result.html', message='An unexpected error occurred.', ok=False)

    return flask.render_template('partials/action_result.html', message='Settings saved.', ok=True)


@account_bp.route('/password', methods=['POST'])
@account_required
def route_change_password() -> str:

    username = flask.g.account_user
    client_ip = flask.request.remote_addr or 'unknown'

    form = ChangePasswordForm()

    # Middleware only knows (ip, username) pair for Subsonic params so check it here
    if rate_limiter.is_blocked(client_ip, username):
        return flask.render_template('partials/action_result.html', message='Too many failed attempts. Try again later.', ok=False)

    if not form.validate_on_submit():
        return flask.render_template('partials/action_result.html', message=' '.join(form_error_messages(form)), ok=False)

    ok, _ = webui_login(username, form.current_password.data)
    if not ok:
        rate_limiter.record(client_ip, username)
        return flask.render_template('partials/action_result.html', message='Current password is incorrect.', ok=False)

    try:
        update_user(username, password=form.password.data)
    except ValueError as e:
        return flask.render_template('partials/action_result.html', message=str(e), ok=False)

    rate_limiter.reset(client_ip, username)
    # Other sessions now invalid, current one stays because it has the new stamp
    flask.session['pv'] = session_stamp(username)
    return flask.render_template('partials/action_result.html', message='Password changed.', ok=True)


@account_bp.route('/apikey', methods=['POST'])
@account_required
def route_regenerate_api_key() -> str:

    username = flask.g.account_user

    try:
        raw_api_key = regenerate_api_key(username)
    except ValueError as e:
        return flask.render_template('partials/action_result.html', message=str(e), ok=False)

    card = flask.render_template('partials/account_api_key.html', new_api_key={'username': username, 'key': raw_api_key})
    return (flask.render_template('partials/action_result.html', message='API key regenerated. The old key no longer works.', ok=True)
            + f'<div id="newApiKey" hx-swap-oob="innerHTML">{card}</div>')


def _avatar_partial(message: str | None = None, ok: bool = True) -> str:
    user = get_userdata(flask.g.account_user, fields=['username', 'avatarLastChanged'])
    return flask.render_template('partials/account_avatar.html', user=user, message=message, ok=ok)


@account_bp.route('/avatar', methods=['POST'])
@account_required
def route_upload_avatar() -> str:

    error = save_uploaded_avatar(flask.g.account_user)
    if error:
        return _avatar_partial(error, ok=False)

    return _avatar_partial('Avatar updated.')


@account_bp.route('/avatar/delete', methods=['POST'])
@account_required
def route_delete_avatar() -> str:
    set_user_avatar(flask.g.account_user, None)
    return _avatar_partial('Avatar removed.')


@account_bp.route('/avatar', methods=['GET'])
@account_required
def route_serve_avatar() -> flask.Response:
    return avatar_response(flask.g.account_user)
