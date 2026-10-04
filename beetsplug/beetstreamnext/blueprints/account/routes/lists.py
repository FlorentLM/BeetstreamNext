from __future__ import annotations

from functools import wraps
import flask

from .. import account_bp, account_required

from beetsplug.beetstreamnext.core.accounts.users_crud import get_user_roles
from beetsplug.beetstreamnext.core.media.shares import list_shares, delete_share


def podcast_role_required(f):
    @wraps(f)
    @account_required
    def decorated(*args, **kwargs):
        if not get_user_roles(flask.g.account_user).get('podcastRole'):
            flask.abort(403)
        return f(*args, **kwargs)

    return decorated


def _podcasts_partial(username: str, message: str | None = None, ok: bool = True, notices: list | None = None) -> str:
    return flask.render_template('partials/account_podcasts.html',
                                 podcasts=flask.current_app.config['podcast_manager'].subscribed_channels(username),
                                 message=message, ok=ok, notices=notices)


def _shares_partial(username: str) -> str:
    return flask.render_template('partials/shares_table.html', shares=list_shares(username), admin=False,
                                 delete_endpoint='account.route_delete_my_share')


@account_bp.route('/shares', methods=['GET'])
@account_required
def route_my_shares() -> str:
    return _shares_partial(flask.g.account_user)


@account_bp.route('/shares/delete/<share_id>', methods=['POST'])
@account_required
def route_delete_my_share(share_id: str) -> str:

    username = flask.g.account_user
    delete_share(share_id, username)     # users can only delete their own shares

    return _shares_partial(username)


@account_bp.route('/podcasts', methods=['GET'])
@podcast_role_required
def route_my_podcasts() -> str:
    return _podcasts_partial(flask.g.account_user)


@account_bp.route('/podcasts/unsubscribe/<int:channel_id>', methods=['POST'])
@podcast_role_required
def route_unsubscribe_podcast(channel_id: int) -> str:
    username = flask.g.account_user
    flask.current_app.config['podcast_manager'].unsubscribe(username, channel_id)
    return _podcasts_partial(username)


@account_bp.route('/podcasts/add', methods=['POST'])
@podcast_role_required
def route_add_podcast() -> str:

    username = flask.g.account_user
    url = (flask.request.form.get('url') or '').strip()

    channel_id, error = flask.current_app.config['podcast_manager'].create_channel(username, url)
    if channel_id is None:
        return _podcasts_partial(username, f"Could not subscribe to podcast feed '{url}': {error}", ok=False)

    return _podcasts_partial(username, 'Subscribed.')


@account_bp.route('/podcasts/import-opml', methods=['POST'])
@podcast_role_required
def route_import_podcast_opml() -> str:

    username = flask.g.account_user
    notices = flask.current_app.config['podcast_manager'].import_opml_upload(username)

    return _podcasts_partial(username, notices=notices)


@account_bp.route('/podcasts/export-opml', methods=['GET'])
@podcast_role_required
def route_export_podcast_opml() -> flask.Response:
    return flask.current_app.config['podcast_manager'].send_opml(flask.g.account_user)


@account_bp.route('/podcasts/discover', methods=['GET'])
@podcast_role_required
def route_discover_podcasts() -> str:
    return flask.current_app.config['podcast_manager'].render_discovery(flask.request.args.get('q'))
