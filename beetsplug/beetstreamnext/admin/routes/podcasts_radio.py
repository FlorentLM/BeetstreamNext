import flask

from .. import admin_bp, admin_required, back_to

from beetsplug.beetstreamnext.constants import FEEDPARSER, MAX_AVATAR_DIM, MAX_AVATAR_BYTES
from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.images import sniff_image, resize_image, ImageTooLarge, send_radio_art, send_podcast_art
from beetsplug.beetstreamnext.core.radio import create_station, update_station, delete_station
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

    create_station(safe_str(form.name.data), form.streamUrl.data, form.homepageUrl.data or None, image)
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
