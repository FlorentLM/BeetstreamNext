from __future__ import annotations

from functools import wraps
import flask

from .. import account_bp, account_required

from beetsplug.beetstreamnext.blueprints import views
from beetsplug.beetstreamnext.core.accounts.users_crud import get_user_roles
from beetsplug.beetstreamnext.core.media.shares import delete_share
from beetsplug.beetstreamnext.core.storage.connection import database


def podcast_role_required(f):
    @wraps(f)
    @account_required
    def decorated(*args, **kwargs):
        if not get_user_roles(flask.g.account_user).get('podcastRole'):
            flask.abort(403)
        return f(*args, **kwargs)

    return decorated


def _podcasts_partial(username: str, message: str | None = None, ok: bool = True, notices: list | None = None) -> str:
    return views.render_channels(
        'account', username, message=message, ok=ok, notices=notices)


def _subscribed_or_404(username: str, channel_id: int) -> None:
    """Users can only act on channels they are subscribed to."""

    with database() as db:
        row = db.execute(
            """
            SELECT 1
            FROM podcast_subscriptions
            WHERE username = ? AND channel_id = ?
            """, (username, channel_id)
        ).fetchone()

    if not row:
        flask.abort(404)


@account_bp.route('/shares', methods=['GET'])
@account_required
def route_my_shares() -> str:
    return views.render_shares(flask.g.account_user)


@account_bp.route('/shares/delete/<share_id>', methods=['POST'])
@account_required
def route_delete_my_share(share_id: str) -> str:

    username = flask.g.account_user
    delete_share(share_id, username)     # users can only delete their own shares

    return views.render_shares(username)


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


@account_bp.route('/podcasts/<int:channel_id>/refresh', methods=['POST'])
@podcast_role_required
def route_refresh_podcast(channel_id: int) -> str:
    username = flask.g.account_user
    _subscribed_or_404(username, channel_id)

    flask.current_app.config['podcast_manager'].background_refresh(channel_id)

    return _podcasts_partial(username, 'Refreshing channel in the background.')


@account_bp.route('/podcasts/<int:channel_id>/download-recents', methods=['POST'])
@podcast_role_required
def route_download_recent_podcast_episodes(channel_id: int) -> str:
    username = flask.g.account_user
    _subscribed_or_404(username, channel_id)

    podcast_manager = flask.current_app.config['podcast_manager']
    count = podcast_manager.download_recent_episodes(channel_id, username=username)

    return _podcasts_partial(username, views.download_recents_message(count))


@account_bp.route('/podcasts/<int:channel_id>/delete-downloads', methods=['POST'])
@podcast_role_required
def route_delete_podcast_downloads(channel_id: int) -> str:
    """Releases this user's downloads for the channel, if other users still want, files are kept."""

    username = flask.g.account_user
    _subscribed_or_404(username, channel_id)

    podcast_manager = flask.current_app.config['podcast_manager']
    count = podcast_manager.release_channel_downloads(username, channel_id)

    return _podcasts_partial(username, views.delete_downloads_message(count))


def _episodes_partial(username: str, channel_id: int) -> str:
    return views.render_episodes('account', channel_id, username)


@account_bp.route('/podcasts/<int:channel_id>/episodes', methods=['GET'])
@podcast_role_required
def route_podcast_episodes(channel_id: int) -> str:
    username = flask.g.account_user
    _subscribed_or_404(username, channel_id)

    return _episodes_partial(username, channel_id)


def _episode_channel(episode_id: int) -> int:

    with database() as db:
        row = db.execute(
            """
            SELECT channel_id
            FROM podcast_episodes
            WHERE id = ?
            """, (episode_id,)
        ).fetchone()

    if not row:
        flask.abort(404)

    return row['channel_id']


@account_bp.route('/podcasts/episode/<int:episode_id>/download', methods=['POST'])
@podcast_role_required
def route_download_podcast_episode(episode_id: int) -> str:
    username = flask.g.account_user
    channel_id = _episode_channel(episode_id)
    _subscribed_or_404(username, channel_id)

    flask.current_app.config['podcast_manager'].request_episode_download(username, episode_id)

    return _episodes_partial(username, channel_id)


@account_bp.route('/podcasts/episode/<int:episode_id>/delete', methods=['POST'])
@podcast_role_required
def route_delete_podcast_episode(episode_id: int) -> str:
    username = flask.g.account_user
    channel_id = _episode_channel(episode_id)
    _subscribed_or_404(username, channel_id)

    flask.current_app.config['podcast_manager'].release_episode_download(username, episode_id)

    return _episodes_partial(username, channel_id)


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
    return views.opml_response(flask.g.account_user)


@account_bp.route('/podcasts/discover', methods=['GET'])
@podcast_role_required
def route_discover_podcasts() -> str:
    return views.render_discovery(flask.request.args.get('q'))
