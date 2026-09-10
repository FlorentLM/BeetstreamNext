import flask
from io import BytesIO

from .. import admin_bp, admin_required, back_to

from beetsplug.beetstreamnext.constants import FEEDPARSER, MAX_AVATAR_DIM, MAX_AVATAR_BYTES
from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.images import sniff_image, resize_image, ImageTooLarge, send_radio_art, send_podcast_art
from beetsplug.beetstreamnext.core.radio import create_station, update_station, delete_station, resolve_station_icon
from beetsplug.beetstreamnext.core.external import query_radio_browser, query_podcastindex
from beetsplug.beetstreamnext.admin.forms import RadioStationForm
from beetsplug.beetstreamnext.utils.text import safe_str


def _flash_form_errors(form) -> None:
    for field_name, errors in form.errors.items():
        for error in errors:
            flask.flash(f'{field_name}: {error}', 'error')


def _uploaded_image() -> bytes | None:
    """Reads + validates the image file field."""
    file = flask.request.files.get('image')
    if file is None or not file.filename:
        return None

    data = file.read(MAX_AVATAR_BYTES + 1)

    if len(data) > MAX_AVATAR_BYTES:
        raise ValueError(f'Image too large (max {MAX_AVATAR_BYTES // 1024} KB).')

    if sniff_image(data) is None:
        raise ValueError('Unsupported or corrupt image. Use JPEG, PNG or WebP.')

    try:
        return resize_image(data, size=MAX_AVATAR_DIM, crop=True).getvalue()

    except (ImageTooLarge, OSError):
        raise ValueError('Unsupported, corrupt, or oversized image.')


##
# Radio stations

@admin_bp.route('/radios/create', methods=['POST'])
@admin_required
def route_create_radio() -> flask.Response:
    form = RadioStationForm()

    if not form.validate_on_submit():
        _flash_form_errors(form)
        return back_to('radios')

    try:
        image = _uploaded_image()
    except ValueError as e:
        flask.flash(str(e), 'error')
        return back_to('radios')

    favicon_url = (flask.request.form.get('favicon_url') or '').strip() or None
    create_station(safe_str(form.name.data), form.streamUrl.data, form.homepageUrl.data or None, image, favicon_url)
    flask.flash(f"Radio station '{form.name.data}' created.", 'success')
    return back_to('radios')


@admin_bp.route('/radios/update/<int:station_id>', methods=['POST'])
@admin_required
def route_update_radio(station_id: int) -> flask.Response:

    form = RadioStationForm()

    if not form.validate_on_submit():
        _flash_form_errors(form)
        return back_to('radios')

    try:
        image = _uploaded_image()
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

    update_station(station_id, safe_str(form.name.data), form.streamUrl.data, form.homepageUrl.data or None, image)
    flask.flash(f"Radio station '{form.name.data}' updated.", 'success')

    return back_to('radios')


@admin_bp.route('/radios/delete/<int:station_id>', methods=['POST'])
@admin_required
def route_delete_radio(station_id: int) -> flask.Response:
    delete_station(station_id)
    flask.flash('Radio station deleted.', 'info')

    return back_to('radios')


@admin_bp.route('/radios/discover', methods=['GET'])
@admin_required
def route_discover_radios() -> flask.Response:

    if not flask.current_app.config.get('enable_radio_discovery'):
        return flask.jsonify({'ok': False, 'message': 'Radio discovery is disabled.', 'stations': []})

    q = (flask.request.args.get('q') or '').strip()
    if not q:
        return flask.jsonify({'ok': False, 'message': 'Enter a station name to search.', 'stations': []})

    stations = query_radio_browser(q, limit=15)
    if not stations:
        return flask.jsonify({'ok': False, 'message': 'No stations found.', 'stations': []})

    plur = 's' if len(stations) > 1 else ''
    return flask.jsonify({
        'ok': True,
        'message': f'Found {len(stations)} station{plur}.',
        'stations': [
            {
                'name': s['name'],
                'stream_url': s['stream_url'],
                'homepage_url': s['homepage_url'],
                'favicon': s.get('favicon') or '',
            }
            for s in stations
        ],
    })


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

    response = send_radio_art(station_id)
    if response is None:
        flask.abort(404)

    return response


##
# Podcasts

@admin_bp.route('/podcasts/add', methods=['POST'])
@admin_required
def route_add_podcast() -> flask.Response:

    if not FEEDPARSER:
        flask.flash("Podcast feeds need the 'feedparser' package to be installed on the server.", 'error')
        return back_to('podcasts')

    url = (flask.request.form.get('url') or '').strip()
    if not url:
        flask.flash('Feed URL is required.', 'error')
        return back_to('podcasts')

    podcast_manager = flask.current_app.config['podcast_manager']
    channel_id, error = podcast_manager.create_channel(flask.session.get('username'), url)

    if channel_id is None:
        flask.flash(f"Could not subscribe to podcast feed '{url}': {error}", 'error')
    else:
        flask.flash('Podcast channel added.', 'success')

    return back_to('podcasts')


@admin_bp.route('/podcasts/discover', methods=['GET'])
@admin_required
def route_discover_podcasts() -> flask.Response:

    if not flask.current_app.config.get('enable_podcast_discovery'):
        return flask.jsonify({'ok': False, 'message': 'Podcast discovery is disabled.', 'feeds': []})

    q = (flask.request.args.get('q') or '').strip()
    if not q:
        return flask.jsonify({'ok': False, 'message': 'Enter a search term.', 'feeds': []})

    feeds = query_podcastindex(q, limit=15)
    if not feeds:
        return flask.jsonify({'ok': False, 'message': 'No podcasts found.', 'feeds': []})

    plur = 's' if len(feeds) > 1 else ''
    return flask.jsonify({
        'ok': True,
        'message': f'Found {len(feeds)} podcast{plur}.',
        'feeds': feeds,
    })


@admin_bp.route('/podcasts/refresh', methods=['POST'])
@admin_required
def route_refresh_all_podcasts() -> flask.Response:

    podcast_manager = flask.current_app.config['podcast_manager']
    podcast_manager.background_refresh()
    flask.flash('Refreshing all podcast channels in the background.', 'info')

    return back_to('podcasts')


@admin_bp.route('/podcasts/<int:channel_id>/refresh', methods=['POST'])
@admin_required
def route_refresh_podcast(channel_id: int) -> flask.Response:

    podcast_manager = flask.current_app.config['podcast_manager']
    podcast_manager.background_refresh(channel_id)
    flask.flash('Refreshing channel in the background.', 'info')

    return back_to('podcasts')


@admin_bp.route('/podcasts/<int:channel_id>/delete', methods=['POST'])
@admin_required
def route_delete_podcast(channel_id: int) -> flask.Response:

    podcast_manager = flask.current_app.config['podcast_manager']
    podcast_manager.delete_channel(channel_id)
    flask.flash('Podcast channel deleted for all subscribers.', 'info')

    return back_to('podcasts')


@admin_bp.route('/podcasts/<int:channel_id>/image', methods=['GET'])
@admin_required
def route_serve_podcast_image(channel_id: int) -> flask.Response:
    response = send_podcast_art(channel_id)
    if response is None:
        flask.abort(404)
    return response


@admin_bp.route('/podcasts/episode/<int:episode_id>/download', methods=['POST'])
@admin_required
def route_download_podcast_episode(episode_id: int) -> flask.Response:

    podcast_manager = flask.current_app.config['podcast_manager']
    if podcast_manager.background_download(episode_id):
        flask.flash('Episode download started.', 'info')
    else:
        flask.flash('This episode has no known audio source.', 'error')

    return back_to('podcasts')


@admin_bp.route('/podcasts/episode/<int:episode_id>/delete', methods=['POST'])
@admin_required
def route_delete_podcast_episode(episode_id: int) -> flask.Response:

    podcast_manager = flask.current_app.config['podcast_manager']
    podcast_manager.delete_episode(episode_id)
    flask.flash('Episode file removed for all subscribers.', 'info')

    return back_to('podcasts')


@admin_bp.route('/podcasts/status', methods=['GET'])
@admin_required
def route_podcast_status() -> flask.Response:
    """Live channel/episode status, polled by the Podcasts tab so it stays current without a manual page reload."""

    with database() as db:
        channel_rows = db.execute(
            """
            SELECT id, status, error_message
            FROM podcast_channels
            """
        ).fetchall()
        episode_rows = db.execute(
            """
            SELECT id, status, file_size, error_message
            FROM podcast_episodes
            """
        ).fetchall()

    return flask.jsonify({
        'channels': {str(r['id']): {'status': r['status'], 'error_message': r['error_message']} for r in channel_rows},
        'episodes': {
            str(r['id']): {'status': r['status'], 'file_size': r['file_size'], 'error_message': r['error_message']}
            for r in episode_rows
        },
    })
