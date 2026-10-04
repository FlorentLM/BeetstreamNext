from __future__ import annotations

import flask

from beetsplug.beetstreamnext.blueprints import views
from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.core.media.images import send_stored_art


def _channels_partial(message: str | None = None, ok: bool = True, notices: list | None = None) -> str:
    return views.render_channels('admin', message=message, ok=ok, notices=notices)


@admin_bp.route('/podcasts/channels', methods=['GET'])
@admin_required
def route_podcast_channels() -> str:
    return _channels_partial()


@admin_bp.route('/podcasts/add', methods=['POST'])
@admin_required
def route_add_podcast() -> str:

    url = (flask.request.form.get('url') or '').strip()
    channel_id, error = flask.current_app.config['podcast_manager'].create_channel(flask.session.get('username'), url)

    if channel_id is None:
        return _channels_partial(f"Could not subscribe to podcast feed '{url}': {error}", ok=False)

    return _channels_partial('Podcast channel added.')


@admin_bp.route('/podcasts/import-opml', methods=['POST'])
@admin_required
def route_import_podcast_opml() -> str:

    notices = flask.current_app.config['podcast_manager'].import_opml_upload(flask.session.get('username'))

    return _channels_partial(notices=notices)


@admin_bp.route('/podcasts/export-opml', methods=['GET'])
@admin_required
def route_export_podcast_opml() -> flask.Response:
    return views.opml_response()


@admin_bp.route('/podcasts/discover', methods=['GET'])
@admin_required
def route_discover_podcasts() -> str:
    return views.render_discovery(flask.request.args.get('q'))


@admin_bp.route('/podcasts/refresh', methods=['POST'])
@admin_required
def route_refresh_all_podcasts() -> str:

    podcast_manager = flask.current_app.config['podcast_manager']
    podcast_manager.background_refresh()

    return _channels_partial('Refreshing all podcast channels in the background.')


@admin_bp.route('/podcasts/<int:channel_id>/refresh', methods=['POST'])
@admin_required
def route_refresh_podcast(channel_id: int) -> str:

    podcast_manager = flask.current_app.config['podcast_manager']
    podcast_manager.background_refresh(channel_id)

    return _channels_partial('Refreshing channel in the background.')


@admin_bp.route('/podcasts/<int:channel_id>/download-recents', methods=['POST'])
@admin_required
def route_download_recent_podcast_episodes(channel_id: int) -> str:

    podcast_manager = flask.current_app.config['podcast_manager']
    count = podcast_manager.download_recent_episodes(channel_id, username=flask.session.get('username'))

    return _channels_partial(views.download_recents_message(count))


@admin_bp.route('/podcasts/<int:channel_id>/delete-downloads', methods=['POST'])
@admin_required
def route_delete_podcast_downloads(channel_id: int) -> str:

    podcast_manager = flask.current_app.config['podcast_manager']
    count = podcast_manager.delete_downloaded_episodes(channel_id)

    return _channels_partial(views.delete_downloads_message(count))


@admin_bp.route('/podcasts/<int:channel_id>/delete', methods=['POST'])
@admin_required
def route_delete_podcast(channel_id: int) -> str:

    podcast_manager = flask.current_app.config['podcast_manager']
    podcast_manager.delete_channel(channel_id)

    return _channels_partial('Podcast channel deleted for all subscribers.')


@admin_bp.route('/podcasts/<int:channel_id>/image', methods=['GET'])
@admin_required
def route_serve_podcast_image(channel_id: int) -> flask.Response:
    response = send_stored_art('podcast', channel_id)
    if response is None:
        flask.abort(404)
    return response


@admin_bp.route('/podcasts/episode/<int:episode_id>/download', methods=['POST'])
@admin_required
def route_download_podcast_episode(episode_id: int) -> str:

    podcast_manager = flask.current_app.config['podcast_manager']
    if podcast_manager.background_download(episode_id):
        return ''

    return flask.render_template('partials/action_result.html', message='This episode has no known audio source.', ok=False)


@admin_bp.route('/podcasts/episode/<int:episode_id>/cancel-download', methods=['POST'])
@admin_required
def route_cancel_podcast_episode_download(episode_id: int) -> str:

    podcast_manager = flask.current_app.config['podcast_manager']
    if podcast_manager.cancel_download(episode_id):
        return ''

    return flask.render_template('partials/action_result.html', message='This episode is not currently downloading.', ok=False)


@admin_bp.route('/podcasts/episode/<int:episode_id>/delete', methods=['POST'])
@admin_required
def route_delete_podcast_episode(episode_id: int) -> str:

    podcast_manager = flask.current_app.config['podcast_manager']
    podcast_manager.delete_episode(episode_id)

    return ''


@admin_bp.route('/podcasts/<int:channel_id>/episodes', methods=['GET'])
@admin_required
def route_podcast_episodes(channel_id: int) -> str:
    """Episodes table for a channel, lazy-loaded."""

    return views.render_episodes('admin', channel_id)


@admin_bp.route('/podcasts/status', methods=['GET'])
@admin_required
def route_podcast_status() -> flask.Response:
    """Initial read of channel/episode status."""
    return flask.jsonify(flask.current_app.config['podcast_manager'].status_snapshot())
