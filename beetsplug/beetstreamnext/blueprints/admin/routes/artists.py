from __future__ import annotations

import json
from io import BytesIO
import flask

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.blueprints.forms import ArtistImageForm, form_error_messages
from beetsplug.beetstreamnext.blueprints.views import modal_error
from beetsplug.beetstreamnext.application import with_app_context
from beetsplug.beetstreamnext.core.library.indexes import ArtistIndex
from beetsplug.beetstreamnext.core.media.artist_images import (
    list_artists, load_stored, set_image, delete_image, get_image,
    suggest_artists, fetched_image_path, image_version
)
from beetsplug.beetstreamnext.core.services.external.artist_image_fetch import artist_image_fetcher
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
    for row in listed_artists['rows']:
        row['image_url'] = _image_url(row['name'], row['artist_key'], row['uploaded_at'])

    return flask.render_template('partials/artist_images.html', listing=listed_artists, message=message, ok=ok)


@admin_bp.route('/artists', methods=['GET'])
@admin_required
def route_artist_images() -> str:
    return _render_list()


@admin_bp.route('/artists/form', methods=['GET'])
@admin_required
@with_app_context      # Admin routes don't get flask.g.lib from the middleware
def route_artist_image_form() -> str:

    args = flask.request.args
    key = args.get('key', '')
    stored = load_stored().get(key) if key else None

    artist = ArtistIndex.get().lookup(args.get('artist_id') or strip_text(args.get('name', '')))
    name = artist.name if artist else (stored['name'] if stored else strip_text(args.get('name', '')))

    form = ArtistImageForm(formdata=None, data={'name': name, 'artist_id': artist.id if artist else ''})

    return flask.render_template('partials/edit_artist_form.html',
                                 preview_url=_image_url(name, key if stored else '', stored['uploaded_at'] if stored else None),
                                 name_locked=bool(stored or name),   # Opened from a row so artist is already known
                                 is_joint=bool(artist and artist.joint),
                                 deezer_enabled=artist_image_fetcher.enabled,
                                 artist_form=form)


@admin_bp.route('/artists/suggest', methods=['GET'])
@admin_required
def route_artist_suggest() -> str:
    return flask.render_template('partials/artist_suggestions.html',
                                 suggestions=suggest_artists(flask.request.args.get('name', '')))


@admin_bp.route('/artists/save', methods=['POST'])
@admin_required
@with_app_context      # Admin routes don't get flask.g.lib from the middleware
def route_save_artist_image() -> str | flask.Response:

    form = ArtistImageForm()
    if not form.validate_on_submit():
        return modal_error(' '.join(form_error_messages(form)), 'artistImageResult')

    name = strip_text(form.name.data)
    artist = ArtistIndex.get().lookup(form.artist_id.data or name)

    if not artist:
        if ArtistIndex.get().named(name):
            return modal_error(f"Several artists are called '{name}': add the image from the list.", 'artistImageResult')
        return modal_error(f"Artist '{name}' was not found in the library.", 'artistImageResult')

    try:
        image = read_uploaded_image('image')
    except ValueError as e:
        return modal_error(str(e), 'artistImageResult')

    if image is None:
        return modal_error('Choose an image to upload.', 'artistImageResult')

    set_image(artist.keys[0], artist.name, image)
    return _render_list(f"Image saved for '{artist.name}'.")


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

    path = fetched_image_path(flask.request.args.get('name', ''))
    if path.is_file():
        return flask.send_file(path, mimetype='image/jpeg')

    flask.abort(404)


@admin_bp.route('/artists/fetch-all', methods=['POST'])
@admin_required
def route_fetch_all() -> str:
    started, message = artist_image_fetcher.fetch_all()
    return flask.render_template('partials/artist_fetch_status.html',
                          running=artist_image_fetcher.running,
                          progress=artist_image_fetcher.progress,
                          message=None if started else message)


@admin_bp.route('/artists/fetch-all/status', methods=['GET'])
@admin_required
def route_fetch_all_status() -> str:
    return flask.render_template('partials/artist_fetch_status.html',
                                 running=artist_image_fetcher.running,
                                 progress=artist_image_fetcher.progress,
                                 message=None)


@admin_bp.route('/artists/fetch-one', methods=['POST'])
@admin_required
def route_fetch_one() -> str:

    form = flask.request.form
    result = artist_image_fetcher.fetch_one(strip_text(form.get('name', '')), form.get('artist_id', ''))

    fetch_messages = {
        'fallback': 'No match: using the image of {matched_name}.',
        'missing': 'Not found on Deezer.',
        'unavailable': 'Deezer is unreachable.',
        'disabled': 'Fetching is off in the settings.',
        'unknown': 'Pick a library artist first.',
    }

    # A found image shows up in the modal's preview, only problems and fallbacks need a message
    message = fetch_messages.get(result.status, '').format(matched_name=result.matched_name)
    ok = None if result.status in ('fallback', 'missing') else result.found

    response = flask.make_response(flask.render_template('partials/action_result.html', ok=ok, message=message))
    if result.found:
        response.headers['HX-Trigger'] = json.dumps({'artist-image-fetched': {'url': _image_url(result.name)}})
    return response
