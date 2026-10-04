from __future__ import annotations

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.blueprints import views
from beetsplug.beetstreamnext.core.media.shares import delete_share


@admin_bp.route('/shares', methods=['GET'])
@admin_required
def route_shares() -> str:
    return views.render_shares()


@admin_bp.route('/shares/delete/<share_id>', methods=['POST'])
@admin_required
def route_delete_share(share_id: str) -> str:

    delete_share(share_id)

    return views.render_shares()
