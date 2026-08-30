import flask

from .. import api_bp

from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.settings import settings_store
from beetsplug.beetstreamnext.core.jukebox import get_jukebox_player, JukeboxUnavailableException
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.core.mappings import Resolve, Serialise

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

    try:
        if action == 'get':
            payload = {
                'jukeboxPlaylist': {
                    **player.status(),
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

    payload = {
        'jukeboxStatus': player.status()
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)
