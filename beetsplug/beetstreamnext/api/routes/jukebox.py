import time
import flask

from .. import api_bp

from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.settings import settings_store
from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.jukebox import get_jukebox_player, JukeboxBackend, JukeboxUnavailableException
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.core.mappings import Resolve, Serialise


def _sync_now_playing(player: JukeboxBackend, username: str, status: dict) -> None:
    """Reflect the jukebox's current track in the 'now_playing' table."""

    ids = player.track_ids()
    index = status.get('currentIndex', -1)

    with database() as db:
        if status.get('playing') and 0 <= index < len(ids):
            db.execute(
                """
                INSERT INTO now_playing (username, item_id, started_at, player_name, position_ms, state)
                VALUES (?, ?, ?, ?, ?, 'playing')
                ON CONFLICT (username) DO UPDATE SET
                    item_id     = excluded.item_id,
                    started_at  = CASE WHEN item_id = excluded.item_id THEN started_at ELSE excluded.started_at END,
                    player_name = excluded.player_name,
                    position_ms = excluded.position_ms,
                    state       = 'playing'
                """, (username, ids[index], time.time(), f'Jukebox ({player.NAME})', int(status.get('position', 0) * 1000))
            )
        else:
            db.execute("DELETE FROM now_playing WHERE username = ?", (username,))


##
# Endpoint

# Spec: https://opensubsonic.netlify.app/docs/endpoints/jukeboxControl/
@api_bp.route('/jukeboxControl', methods=['GET', 'POST'])
@api_bp.route('/jukeboxControl.view', methods=['GET', 'POST'])
def endpoint_jukebox_control() -> flask.Response:

    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    action = r.get('action', default='', type=safe_str)     # Required

    if not (flask.g.user_data.get('jukeboxRole') or flask.g.user_data.get('adminRole')):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not settings_store.get('jukebox_allowed'):
        return subsonic_error(0, message='Jukebox mode is disabled on this server.', resp_fmt=resp_fmt)

    entry_ids = r.getlist('id', type=safe_str)
    player = get_jukebox_player()
    username = flask.g.username

    try:
        if action == 'get':
            status = player.status()
            _sync_now_playing(player, username, status)
            payload = {
                'jukeboxPlaylist': {
                    **status,
                    'entry': Serialise.playables(player.track_ids())
                }
            }
            return subsonic_response(payload, resp_fmt=resp_fmt)

        elif action == 'status':
            pass

        elif action == 'set':
            player.set_playlist(Resolve.playables(entry_ids))

        elif action == 'add':
            player.add(Resolve.playables(entry_ids))

        elif action == 'clear':
            player.clear()

        elif action == 'remove':
            index = r.get('index', type=int)
            if index is None:
                return subsonic_error(10, message='index is required.', resp_fmt=resp_fmt)
            player.remove(index)

        elif action == 'shuffle':
            player.shuffle()

        elif action == 'start':
            player.start()

        elif action == 'stop':
            player.stop()

        elif action == 'skip':

            index = r.get('index', type=int)
            offset = r.get('offset', default=0.0, type=float)

            if index is None:
                return subsonic_error(10, message='index is required.', resp_fmt=resp_fmt)
            try:
                player.skip(index, offset)
            except ValueError:
                return subsonic_error(10, message='index out of range.', resp_fmt=resp_fmt)

        elif action == 'setGain':
            gain = r.get('gain', type=float)
            if gain is None:
                return subsonic_error(10, message='gain is required.', resp_fmt=resp_fmt)
            player.set_gain(gain)

        else:
            return subsonic_error(10, message=f"Unknown jukeboxControl action '{action}'.", resp_fmt=resp_fmt)

    except JukeboxUnavailableException as e:
        return subsonic_error(0, message=str(e), resp_fmt=resp_fmt)

    status = player.status()
    _sync_now_playing(player, username, status)

    payload = {
        'jukeboxStatus': status
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)
