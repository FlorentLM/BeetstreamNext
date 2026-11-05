from __future__ import annotations

from io import BytesIO
import flask

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.blueprints.forms import ArtistImageForm, form_error_messages
from beetsplug.beetstreamnext.blueprints.views import modal_error
from beetsplug.beetstreamnext.core.media.artist_images import (
    list_images, list_artists, set_image, delete_image, get_image,
    find_artist, suggest_artists, credit_keys,
)
from beetsplug.beetstreamnext.core.media.images import read_uploaded_image, sniff_image
from beetsplug.beetstreamnext.utils.text import strip_text


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
                                 artist=entry,
                                 name_locked=bool(entry or name),   # Opened from a row so artist is already known
                                 is_joint=bool(name and credit_keys(name)),
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
        return _render_list('Artist image removed.')

    return _render_list('No such artist image.', ok=False)


@admin_bp.route('/artists/image', methods=['GET'])
@admin_required
def route_serve_artist_image() -> flask.Response:

    key = flask.request.args.get('key', '')

    image = get_image(key)
    if not image:
        flask.abort(404)

    return flask.send_file(BytesIO(image), mimetype=sniff_image(image) or 'image/jpeg')
