from __future__ import annotations
import flask

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.core.media.shares import list_shares, delete_share


def _shares_partial() -> str:
    return flask.render_template('partials/shares_table.html', shares=list_shares(), admin=True,
                                 delete_endpoint='admin.route_delete_share')


@admin_bp.route('/shares', methods=['GET'])
@admin_required
def route_shares() -> str:
    return _shares_partial()


@admin_bp.route('/shares/delete/<share_id>', methods=['POST'])
@admin_required
def route_delete_share(share_id: str) -> str:

    delete_share(share_id)

    return _shares_partial()
