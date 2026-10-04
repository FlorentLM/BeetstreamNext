from __future__ import annotations
import flask
from io import BytesIO

from .. import admin_bp, admin_required, back_to

from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.images import sniff_image, send_stored_art, read_uploaded_image
from beetsplug.beetstreamnext.core.radio import create_station, update_station, delete_station, list_radios, resolve_station_icon
from beetsplug.beetstreamnext.core.external import query_radio_browser
from beetsplug.beetstreamnext.forms import RadioStationForm, flash_form_errors
from beetsplug.beetstreamnext.utils.text import safe_str, format_duration
from beetsplug.beetstreamnext.utils.general import human_bytes


##
# Radio stations

@admin_bp.route('/radios/create', methods=['POST'])
@admin_required
def route_create_radio() -> flask.Response:
    form = RadioStationForm()

    if not form.validate_on_submit():
        flash_form_errors(form)
        return back_to('radios')

    try:
        image = read_uploaded_image('image')
    except ValueError as e:
        flask.flash(str(e), 'error')
        return back_to('radios')

    favicon_url = (flask.request.form.get('favicon_url') or '').strip() or None
    station_id, error = create_station(
        safe_str(form.name.data), form.streamUrl.data, form.homepageUrl.data or None, image, favicon_url
    )

    if station_id is None:
        flask.flash(f'Could not create radio station: {error}', 'error')
    else:
        flask.flash(f"Radio station '{form.name.data}' created.", 'success')

    return back_to('radios')


@admin_bp.route('/radios/update/<int:station_id>', methods=['POST'])
@admin_required
def route_update_radio(station_id: int) -> flask.Response:

    form = RadioStationForm()

    if not form.validate_on_submit():
        flash_form_errors(form)
        return back_to('radios')

    try:
        image = read_uploaded_image('image')
    except ValueError as e:
        flask.flash(str(e), 'error')
        return back_to('radios')

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
        flask.flash(f'Could not update radio station: {error}', 'error')
    else:
        flask.flash(f"Radio station '{form.name.data}' updated.", 'success')

    return back_to('radios')


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

def _channels_partial(message: str | None = None) -> str:
    channels, total_size = flask.current_app.config['podcast_manager'].channels_overview()
    return flask.render_template('partials/podcast_channels.html', channels=channels, total_size=total_size,
                                 message=message, ok=True)


@admin_bp.route('/podcasts/channels', methods=['GET'])
@admin_required
def route_podcast_channels() -> str:
    return _channels_partial()


@admin_bp.route('/podcasts/add', methods=['POST'])
@admin_required
def route_add_podcast() -> flask.Response:

    url = (flask.request.form.get('url') or '').strip()
    channel_id, error = flask.current_app.config['podcast_manager'].create_channel(flask.session.get('username'), url)

    if channel_id is None:
        flask.flash(f"Could not subscribe to podcast feed '{url}': {error}", 'error')
    else:
        flask.flash('Podcast channel added.', 'success')

    return back_to('podcasts')


@admin_bp.route('/podcasts/import-opml', methods=['POST'])
@admin_required
def route_import_podcast_opml() -> flask.Response:

    flask.current_app.config['podcast_manager'].import_opml_upload(flask.session.get('username'))

    return back_to('podcasts')


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
def route_refresh_all_podcasts() -> flask.Response:

    podcast_manager = flask.current_app.config['podcast_manager']
    podcast_manager.background_refresh()
    flask.flash('Refreshing all podcast channels in the background.', 'info')

    return back_to('podcasts')


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


def _episode_action_done(message: str, category: str = 'info') -> flask.Response:
    """
    Success is silent (SSE status push updates the row), failures flash and force a refresh to display the message.
    """
    if flask.request.headers.get('HX-Request'):
        if category == 'info':
            return flask.Response(status=204)
        flask.flash(message, category)
        response = flask.Response(status=204)
        response.headers['HX-Refresh'] = 'true'
        return response

    flask.flash(message, category)
    return back_to('podcasts')


@admin_bp.route('/podcasts/episode/<int:episode_id>/download', methods=['POST'])
@admin_required
def route_download_podcast_episode(episode_id: int) -> flask.Response:

    podcast_manager = flask.current_app.config['podcast_manager']
    if podcast_manager.background_download(episode_id):
        return _episode_action_done('Episode download started.')

    return _episode_action_done('This episode has no known audio source.', 'error')


@admin_bp.route('/podcasts/episode/<int:episode_id>/cancel-download', methods=['POST'])
@admin_required
def route_cancel_podcast_episode_download(episode_id: int) -> flask.Response:

    podcast_manager = flask.current_app.config['podcast_manager']
    if podcast_manager.cancel_download(episode_id):
        return _episode_action_done('Download cancelled.')

    return _episode_action_done('This episode is not currently downloading.', 'error')


@admin_bp.route('/podcasts/episode/<int:episode_id>/delete', methods=['POST'])
@admin_required
def route_delete_podcast_episode(episode_id: int) -> flask.Response:

    podcast_manager = flask.current_app.config['podcast_manager']
    podcast_manager.delete_episode(episode_id)

    return _episode_action_done('Episode file removed for all subscribers.')


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
            'size_display': human_bytes(r['file_size']) if r['file_size'] else None,
        }
        for r in rows
    ]

    return flask.render_template('partials/podcast_episodes.html', episodes=episodes)


@admin_bp.route('/podcasts/status', methods=['GET'])
@admin_required
def route_podcast_status() -> flask.Response:
    """Initial read of channel/episode status (live updates done over SSE)."""

    return flask.jsonify(flask.current_app.config['podcast_manager'].status_snapshot())
