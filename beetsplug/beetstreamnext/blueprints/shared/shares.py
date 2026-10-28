from __future__ import annotations

from datetime import date
from typing import Callable, Optional
import flask

from beetsplug.beetstreamnext.blueprints import views
from beetsplug.beetstreamnext.blueprints.forms import ShareOptionsForm, form_error_messages
from beetsplug.beetstreamnext.blueprints.shared import route_registrar
from beetsplug.beetstreamnext.blueprints.views import modal_error
from beetsplug.beetstreamnext.core.media.shares import list_shares, update_share


def register_share_routes(bp: flask.Blueprint, login_required: Callable, *, owner_only: bool) -> None:
    """
    Admin panel and account page shares editing: admins can edit any share,
    users can edit their own.
    """
    route = route_registrar(bp, login_required)

    def scope() -> Optional[str]:
        return flask.session['username'] if owner_only else None

    @route('/shares/<share_id>/edit', ['GET'])
    def edit_share(share_id: str) -> str:
        """Pre-filled options form for a share (lazy-loaded)."""

        share = next(iter(list_shares(scope(), share_id)), None)
        if not share:
            flask.abort(404)

        return flask.render_template(
            'partials/edit_share_form.html',
            share=share,
            form=ShareOptionsForm(formdata=None, data={
                'description': share['description'] or '',
                'expires': date.fromtimestamp(share['expires']) if share['expires'] else None,
                'max_downloads': share['max_downloads'],
            }),
            update_endpoint=f'{bp.name}.route_save_share',
        )

    @route('/shares/update/<share_id>', ['POST'])
    def save_share(share_id: str) -> str | flask.Response:
        form = ShareOptionsForm()

        if not form.validate_on_submit():
            return modal_error(' '.join(form_error_messages(form)), 'editShareResult')

        updated = update_share(
            share_id, scope(),
            description=(form.description.data or '').strip(),
            expires_on=form.expires.data,
            max_downloads=form.max_downloads.data,
            new_password=form.password.data,
            remove_password=bool(form.remove_password.data),
        )
        if not updated:
            flask.abort(404)

        return views.render_shares(scope(), 'Share updated.')
