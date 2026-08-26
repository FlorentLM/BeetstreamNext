import secrets
import flask

from .. import api_bp

from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.api.serializers import map_share
from beetsplug.beetstreamnext.utils.text import safe_str


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getshares/
@api_bp.route('/getShares', methods=['GET', 'POST'])
@api_bp.route('/getShares.view', methods=['GET', 'POST'])
def endpoint_get_shares() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    if not flask.g.user_data.get('shareRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    with database() as db:
        shares = db.execute(
            """
            SELECT * FROM shares 
            WHERE username = ?
            """, (flask.g.username,)
        ).fetchall()

        shares_data = []
        for s in shares:
            rows = db.execute(
                """
                SELECT item_id 
                FROM share_entries 
                WHERE share_id = ?
                """, (s['id'],)
            ).fetchall()

            entries = [e[0] for e in rows]
            shares_data.append(map_share(s, entries))

    payload = {
        'shares': {
            'share': shares_data
        }
    }

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/createshare/
@api_bp.route('/createShare', methods=['GET', 'POST'])
@api_bp.route('/createShare.view', methods=['GET', 'POST'])
def endpoint_create_share() -> flask.Response:
    r = flask.request.values

    resp_fmt = r.get('f', default='xml', type=safe_str)
    ids = r.getlist('id', type=safe_str)                    # Required
    description = r.get('description', default='', type=safe_str)
    expires_ms = r.get('expires', default=0, type=int)

    if not flask.g.user_data.get('shareRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not ids:
        return subsonic_error(10, resp_fmt=resp_fmt)

    share_id = secrets.token_urlsafe(12)
    expires = expires_ms / 1000.0 if expires_ms > 0 else None

    with database() as db:
        db.execute(
            """
            INSERT INTO shares (id, username, description, expires) 
            VALUES (?, ?, ?, ?)
            """, (share_id, flask.g.username, description, expires)
        )
        for item_id in ids:
            db.execute(
                """
                INSERT INTO share_entries (share_id, item_id) 
                VALUES (?, ?)
                """, (share_id, item_id)
            )

    # Refetch for response
    with database() as db:
        row = db.execute(
            """
            SELECT * FROM shares 
            WHERE id = ?
            """, (share_id,)
        ).fetchone()

    payload = {
        'shares': {
            'share': [map_share(row, ids)]
        }
    }

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/updateshare/
@api_bp.route('/updateShare', methods=['GET', 'POST'])
@api_bp.route('/updateShare.view', methods=['GET', 'POST'])
def endpoint_update_share() -> flask.Response:
    r = flask.request.values

    resp_fmt = r.get('f', default='xml', type=safe_str)
    share_id = r.get('id', default='', type=safe_str)       # Required
    description = r.get('description', default=None, type=safe_str)
    expires_ms = r.get('expires', default=None, type=int)

    if not flask.g.user_data.get('shareRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not share_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    with database() as db:
        row = db.execute(
            """
            SELECT username
            FROM shares
            WHERE id = ?
            """, (share_id,)
        ).fetchone()

        if not row:
            return subsonic_error(70, resp_fmt=resp_fmt)

        # Only the creator or an admin can update the share
        is_admin = bool(flask.g.user_data.get('adminRole'))
        if row['username'] != flask.g.username and not is_admin:
            return subsonic_error(50, resp_fmt=resp_fmt)

        updates = []
        params = []

        if description is not None:
            updates.append("description = ?")
            params.append(description)

        if expires_ms is not None:
            updates.append("expires = ?")
            params.append(expires_ms / 1000.0 if expires_ms > 0 else None)

        if updates:
            params.append(share_id)
            db.execute(f"""UPDATE shares SET {', '.join(updates)} WHERE id = ?""", params)

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/deleteshare/
@api_bp.route('/deleteShare', methods=['GET', 'POST'])
@api_bp.route('/deleteShare.view', methods=['GET', 'POST'])
def endpoint_delete_share() -> flask.Response:
    r = flask.request.values

    resp_fmt = r.get('f', default='xml', type=safe_str)
    share_id = r.get('id', type=safe_str)                   # Required

    if not flask.g.user_data.get('shareRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not share_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    with database() as db:
        # Only allow deleting own shares unless admin
        if flask.g.user_data.get('adminRole'):
            db.execute("""DELETE FROM shares WHERE id = ?""", (share_id,))
        else:
            db.execute("""DELETE FROM shares WHERE id = ? AND username = ?""", (share_id, flask.g.username))

    return subsonic_response({}, resp_fmt=resp_fmt)