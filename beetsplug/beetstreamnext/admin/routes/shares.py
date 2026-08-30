import flask

from .. import admin_bp, admin_required, back_to

from beetsplug.beetstreamnext.core.database import database


@admin_bp.route('/shares/delete/<share_id>', methods=['POST'])
@admin_required
def route_delete_share(share_id: str) -> flask.Response:

    with database() as db:
        db.execute("""DELETE FROM shares WHERE id = ?""", (share_id,))

    flask.flash(f"Share '{share_id}' deleted successfully.", 'success')

    return back_to('shares')
