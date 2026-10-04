from __future__ import annotations

from io import BytesIO
from typing import Callable
import flask

from beetsplug.beetstreamnext.blueprints import views
from beetsplug.beetstreamnext.blueprints.views import modal_error, radio_endpoints
from beetsplug.beetstreamnext.blueprints.forms import RadioStationForm, form_error_messages
from beetsplug.beetstreamnext.core.media.images import sniff_image, send_stored_art, read_uploaded_image
from beetsplug.beetstreamnext.core.services.radio import create_station, update_station, delete_station, resolve_station_icon
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.utils.text import safe_str


def register_radio_routes(bp: flask.Blueprint, login_required: Callable) -> None:
    """
    Admin panel and account page expose the same routes, only the auth decorator differs.
    """

    bp_name = bp.name

    def route(rule: str, endpoint: str, methods: list[str]) -> Callable:
        def register(view: Callable) -> Callable:
            bp.add_url_rule(rule, endpoint=endpoint, view_func=login_required(view), methods=methods)
            return view
        return register

    def render(message: str | None = None) -> str:
        return views.render_radios(flask.session['username'], message, bp_name=bp_name)

    def own_station_or_404(station_id: int) -> dict:
        with database() as db:
            row = db.execute(
                """
                SELECT id, name, stream_url, homepage_url, image, (image IS NOT NULL) AS has_image
                FROM internet_radio_stations
                WHERE id = ? AND owner = ?
                """, (station_id, flask.session['username'])
            ).fetchone()

        if not row:
            flask.abort(404)

        return dict(row)

    @route('/radios', 'route_radios', ['GET'])
    def radios() -> str:
        return render()

    @route('/radios/create', 'route_create_radio', ['POST'])
    def create_radio() -> str | flask.Response:
        form = RadioStationForm()

        if not form.validate_on_submit():
            return modal_error(' '.join(form_error_messages(form)), 'createRadioResult')

        try:
            image = read_uploaded_image('image')
        except ValueError as e:
            return modal_error(str(e), 'createRadioResult')

        favicon_url = (flask.request.form.get('favicon_url') or '').strip() or None
        station_id, error = create_station(
            flask.session['username'], safe_str(form.name.data), form.streamUrl.data, form.homepageUrl.data or None,
            image, favicon_url
        )

        if station_id is None:
            return modal_error(f'Could not create radio station: {error}', 'createRadioResult')

        return render(f"Radio station '{form.name.data}' created.")

    @route('/radios/update/<int:station_id>', 'route_update_radio', ['POST'])
    def update_radio(station_id: int) -> str | flask.Response:
        station = own_station_or_404(station_id)
        form = RadioStationForm()

        if not form.validate_on_submit():
            return modal_error(' '.join(form_error_messages(form)), 'editRadioResult')

        try:
            image = read_uploaded_image('image')
        except ValueError as e:
            return modal_error(str(e), 'editRadioResult')

        if image is None and not flask.request.form.get('remove_image'):
            image = station['image']

        error = update_station(station_id, safe_str(form.name.data), form.streamUrl.data, form.homepageUrl.data or None,
                               image, owner=flask.session['username'])
        if error:
            return modal_error(f'Could not update radio station: {error}', 'editRadioResult')

        return render(f"Radio station '{form.name.data}' updated.")

    @route('/radios/<int:station_id>/edit', 'route_edit_radio', ['GET'])
    def edit_radio(station_id: int) -> str:
        """Pre-filled edit form for a radio station, lazy-loaded."""
        station = own_station_or_404(station_id)

        return flask.render_template(
            'partials/edit_radio_form.html',
            station=station,
            radio_ep=radio_endpoints(bp_name),
            radio_form=RadioStationForm(formdata=None, data={
                'name': station['name'],
                'streamUrl': station['stream_url'],
                'homepageUrl': station['homepage_url'],
            }),
        )

    @route('/radios/delete/<int:station_id>', 'route_delete_radio', ['POST'])
    def delete_radio(station_id: int) -> str:
        delete_station(station_id, owner=flask.session['username'])
        return render('Radio station deleted.')

    @route('/radios/discover', 'route_discover_radios', ['GET'])
    def discover_radios() -> str:
        return views.render_radio_discovery(flask.request.args.get('q'))

    @route('/radios/favicon-proxy', 'route_radio_favicon_proxy', ['GET'])
    def radio_favicon_proxy() -> flask.Response:
        """Same-origin preview of a radio search result's icon."""
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

    @route('/radios/<int:station_id>/image', 'route_serve_radio_image', ['GET'])
    def serve_radio_image(station_id: int) -> flask.Response:
        own_station_or_404(station_id)

        response = send_stored_art('radio', station_id)
        if response is None:
            flask.abort(404)

        return response
