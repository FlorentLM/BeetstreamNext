from __future__ import annotations
import flask
from io import BytesIO

from beetsplug.beetstreamnext.utils.htmx import modal_error
from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.core.media.images import sniff_image, send_stored_art, read_uploaded_image
from beetsplug.beetstreamnext.core.services.radio import create_station, update_station, delete_station, list_radios, resolve_station_icon
from beetsplug.beetstreamnext.core.services.external.radio_browser import query_radio_browser
from beetsplug.beetstreamnext.blueprints.forms import RadioStationForm, form_error_messages
from beetsplug.beetstreamnext.utils.text import safe_str, format_duration, format_bytes


##
# Radio stations

@admin_bp.route('/radios/create', methods=['POST'])
@admin_required
def route_create_radio() -> str | flask.Response:
    form = RadioStationForm()

    if not form.validate_on_submit():
        return modal_error(' '.join(form_error_messages(form)), 'createRadioResult')

    try:
        image = read_uploaded_image('image')
    except ValueError as e:
        return modal_error(str(e), 'createRadioResult')

    favicon_url = (flask.request.form.get('favicon_url') or '').strip() or None
    station_id, error = create_station(
        safe_str(form.name.data), form.streamUrl.data, form.homepageUrl.data or None, image, favicon_url
    )

    if station_id is None:
        return modal_error(f'Could not create radio station: {error}', 'createRadioResult')

    return _radios_partial(f"Radio station '{form.name.data}' created.")


@admin_bp.route('/radios/update/<int:station_id>', methods=['POST'])
@admin_required
def route_update_radio(station_id: int) -> str | flask.Response:

    form = RadioStationForm()

    if not form.validate_on_submit():
        return modal_error(' '.join(form_error_messages(form)), 'editRadioResult')

    try:
        image = read_uploaded_image('image')
    except ValueError as e:
        return modal_error(str(e), 'editRadioResult')

    if image is None and not flask.request.form.get('remove_image'):

        with database() as db:
            row = db.execute(
                """
                SELECT image 
                FROM internet_radio_stations 
                WHERE id = ?
                """, (station_id,)
            ).fetchone()

        image = row['image'] if row else None

    error = update_station(station_id, safe_str(form.name.data), form.streamUrl.data, form.homepageUrl.data or None, image)

    if error:
        return modal_error(f'Could not update radio station: {error}', 'editRadioResult')

    return _radios_partial(f"Radio station '{form.name.data}' updated.")


@admin_bp.route('/radios/<int:station_id>/edit', methods=['GET'])
@admin_required
def route_edit_radio(station_id: int) -> str:
    """Pre-filled edit form for radio station edit, lazy-loaded."""

    with database() as db:
        row = db.execute(
            """
            SELECT id, name, stream_url, homepage_url, (image IS NOT NULL) AS has_image
            FROM internet_radio_stations
            WHERE id = ?
            """, (station_id,)
        ).fetchone()

    if not row:
        flask.abort(404)

    station = dict(row)

    return flask.render_template(
        'partials/edit_radio_form.html',
        station=station,
        radio_form=RadioStationForm(formdata=None, data={
            'name': station['name'],
            'streamUrl': station['stream_url'],
            'homepageUrl': station['homepage_url'],
        }),
    )


def _radios_partial(message: str | None = None) -> str:
    return flask.render_template('partials/radio_list.html', radios=list_radios(), message=message, ok=True)


@admin_bp.route('/radios', methods=['GET'])
@admin_required
def route_radios() -> str:
    return _radios_partial()


@admin_bp.route('/radios/delete/<int:station_id>', methods=['POST'])
@admin_required
def route_delete_radio(station_id: int) -> str:
    delete_station(station_id)

    return _radios_partial('Radio station deleted.')


@admin_bp.route('/radios/discover', methods=['GET'])
@admin_required
def route_discover_radios() -> str:

    if not flask.current_app.config.get('enable_radio_discovery'):
        return flask.render_template('partials/radio_search.html', stations=[], message='Radio discovery is disabled.')

    q = (flask.request.args.get('q') or '').strip()
    if not q:
        return flask.render_template('partials/radio_search.html', stations=[], message='Enter a station name to search.')

    stations = query_radio_browser(q, limit=15)
    if not stations:
        return flask.render_template('partials/radio_search.html', stations=[], message='No stations found.')

    return flask.render_template(
        'partials/radio_search.html',
        stations=[
            {
                'name': s['name'],
                'stream_url': s['stream_url'],
                'homepage_url': s['homepage_url'],
                'favicon': s.get('favicon') or '',
            }
            for s in stations
        ],
        message=None,
    )


@admin_bp.route('/radios/favicon-proxy', methods=['GET'])
@admin_required
def route_radio_favicon_proxy() -> flask.Response:
    """
    Same-origin preview of a radio search result's icon
    """
    name = (flask.request.args.get('name') or '').strip()
    url = (flask.request.args.get('url') or '').strip()
    homepage = (flask.request.args.get('homepage') or '').strip()
    if not name:
        flask.abort(404)

    image = resolve_station_icon(name, url or None, homepage or None)
    mimetype = sniff_image(image) if image else None
    if not mimetype:
        flask.abort(404)

    return flask.send_file(BytesIO(image), mimetype=mimetype)


@admin_bp.route('/radios/<int:station_id>/image', methods=['GET'])
@admin_required
def route_serve_radio_image(station_id: int) -> flask.Response:

    response = send_stored_art('radio', station_id)
    if response is None:
        flask.abort(404)

    return response


##
# Podcasts

def _channels_partial(message: str | None = None, ok: bool = True, notices: list | None = None) -> str:
    channels, total_size = flask.current_app.config['podcast_manager'].channels_overview()
    return flask.render_template(
        'partials/podcast_channels.html',
        channels=channels,
        total_size=total_size,
        message=message,
        ok=ok,
        notices=notices
    )


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
    return flask.current_app.config['podcast_manager'].send_opml()


@admin_bp.route('/podcasts/discover', methods=['GET'])
@admin_required
def route_discover_podcasts() -> str:
    return flask.current_app.config['podcast_manager'].render_discovery(flask.request.args.get('q'))


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

    if count:
        message = f"Downloading {count} recent episode{'s' if count != 1 else ''}."
    else:
        message = ("No episodes to download (already downloaded/downloading, or "
                   "'podcast_auto_download_count' is set to 0).")

    return _channels_partial(message)


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

    with database() as db:
        rows = db.execute(
            """
            SELECT id, title, publish_date, duration, status, file_size, error_message
            FROM podcast_episodes
            WHERE channel_id = ?
            ORDER BY publish_date DESC
            """, (channel_id,)
        ).fetchall()

    episodes = [
        {
            **dict(r),
            'duration_display': format_duration(r['duration']),
            'size_display': format_bytes(r['file_size']) if r['file_size'] else None,
        }
        for r in rows
    ]

    return flask.render_template('partials/podcast_episodes.html', episodes=episodes)


@admin_bp.route('/podcasts/status', methods=['GET'])
@admin_required
def route_podcast_status() -> flask.Response:
    """Initial read of channel/episode status."""
    return flask.jsonify(flask.current_app.config['podcast_manager'].status_snapshot())
