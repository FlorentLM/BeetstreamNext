from __future__ import annotations

from io import BytesIO
import flask

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.blueprints.forms import ArtistImageForm, form_error_messages
from beetsplug.beetstreamnext.blueprints.views import modal_error
from beetsplug.beetstreamnext.core.media.artist_images import (
    list_images, list_artists, set_image, delete_image, get_image,
    find_artist, suggest_artists, credit_keys, artist_image_fetcher, image_version
)
from beetsplug.beetstreamnext.core.media.images import read_uploaded_image, sniff_image
from beetsplug.beetstreamnext.utils.text import strip_text


def _image_url(name: str, key: str = '', uploaded_at: int | None = None) -> str | None:
    version = image_version(uploaded_at, name)
    return flask.url_for('admin.route_serve_artist_image', key=key, name=name, v=version) if version else None


def _render_list(message: str | None = None, ok: bool = True) -> str:

    args = flask.request.values
    try:
        page = int(args.get('page', 1))
    except ValueError:
        page = 1

    listed_artists = list_artists(args.get('filter', 'all'), args.get('q', ''), page)

    return flask.render_template('partials/artist_images.html', listing=listed_artists, message=message, ok=ok)


@admin_bp.route('/artists', methods=['GET'])
@admin_required
def route_artist_images() -> str:
    return _render_list()


@admin_bp.route('/artists/form', methods=['GET'])
@admin_required
def route_artist_image_form() -> str:
    key = flask.request.args.get('key')
    entry = next((a for a in list_images() if a['artist_key'] == key), None) if key else None

    name = entry['name'] if entry else strip_text(flask.request.args.get('name', ''))
    form = ArtistImageForm(formdata=None, data={'name': name})

    return flask.render_template('partials/edit_artist_form.html',
                                 preview_url=_image_url(name, key if entry else '', entry['uploaded_at'] if entry else None),
                                 artist=entry,
                                 name_locked=bool(entry or name),   # Opened from a row so artist is already known
                                 is_joint=bool(name and credit_keys(name)),
                                 deezer_enabled=flask.current_app.config['fetch_artists_images'],
                                 artist_form=form)


@admin_bp.route('/artists/suggest', methods=['GET'])
@admin_required
def route_artist_suggest() -> str:
    return flask.render_template('partials/artist_suggestions.html',
                                 suggestions=suggest_artists(flask.request.args.get('name', '')))


@admin_bp.route('/artists/save', methods=['POST'])
@admin_required
def route_save_artist_image() -> str | flask.Response:

    form = ArtistImageForm()
    if not form.validate_on_submit():
        return modal_error(' '.join(form_error_messages(form)), 'artistImageResult')

    found = find_artist(strip_text(form.name.data))   # Must match the library's exact spelling
    if not found:
        return modal_error(f"Artist '{form.name.data}' was not found in the library.", 'artistImageResult')

    name, mbid = found

    try:
        image = read_uploaded_image('image')
    except ValueError as e:
        return modal_error(str(e), 'artistImageResult')

    if image is None:
        return modal_error('Choose an image to upload.', 'artistImageResult')

    set_image(name, mbid, image)
    return _render_list(f"Image saved for '{name}'.")


@admin_bp.route('/artists/delete', methods=['POST'])
@admin_required
def route_delete_artist_image() -> str:

    key = flask.request.form.get('key', '')
    if delete_image(key):
        return _render_list('Image removed.')

    return _render_list('No such image.', ok=False)


@admin_bp.route('/artists/image', methods=['GET'])
@admin_required
def route_serve_artist_image() -> flask.Response:
    """Image shown for an artist: uploaded one (by key) if any, or fetched one (by name)."""

    image = get_image(flask.request.args.get('key', ''))
    if image:
        return flask.send_file(BytesIO(image), mimetype=sniff_image(image) or 'image/jpeg')

    path = artist_image_fetcher.image_path(flask.request.args.get('name', ''))
    if path.is_file():
        return flask.send_file(path, mimetype='image/jpeg')

    flask.abort(404)


@admin_bp.route('/artists/fetch-all', methods=['POST'])
@admin_required
def route_fetch_all() -> str:
    started, message = artist_image_fetcher.start()
    return flask.render_template('partials/artist_fetch_status.html',
                                 fetcher=artist_image_fetcher, message=None if started else message)


@admin_bp.route('/artists/fetch-all/status', methods=['GET'])
@admin_required
def route_fetch_all_status() -> str:
    return flask.render_template('partials/artist_fetch_status.html',
                                 fetcher=artist_image_fetcher, message=None)


@admin_bp.route('/artists/fetch-one', methods=['POST'])
@admin_required
def route_fetch_one() -> str:

    if not flask.current_app.config['fetch_artists_images']:
        return flask.render_template('partials/action_result.html',
                                     ok=False,
                                     message='Fetching is off in the settings.')

    result = artist_image_fetcher.fetch_one(strip_text(flask.request.form.get('name', '')))

    if result is None:
        return flask.render_template('partials/action_result.html',
                                     ok=False,
                                     message='Pick a library artist first.')

    messages = {
        'deezer': 'Found on Deezer.',
        'fallback': f"No match: using the image of {result['matched_name']}.",
        'missing': 'Not found on Deezer.',
        'unavailable': 'Deezer is unreachable.',
    }

    return flask.render_template('partials/action_result.html',
                                 ok=result['status'] != 'unavailable',
                                 message=messages[result['status']],
                                 image_url=_image_url(result['name']) if result['status'] in ('deezer', 'fallback') else None)
