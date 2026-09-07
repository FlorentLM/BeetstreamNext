import flask

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.core.jukebox import (
    sonos_discovery, chromecast_discovery, mpv_discovery, JukeboxUnavailableException,
)


@admin_bp.route('/settings/jukebox/discover-devices', methods=['GET'])
@admin_required
def route_discover_devices() -> flask.Response:
    from beetsplug.beetstreamnext.settings import settings_store

    requested = flask.request.args.get('backend')
    backend = requested if requested in ('server_hardware', 'sonos', 'chromecast') else settings_store.get('jukebox_backend')

    try:
        if backend == 'chromecast':
            kind = 'Chromecasts'
            devices = [
                {'name': d['name'], 'id': d['uuid'], 'detail': d['host']}
                for d in chromecast_discovery()
            ]
        elif backend == 'sonos':
            kind = 'Sonos speakers'
            devices = [
                {'name': s['name'], 'id': s['ip'], 'detail': s['ip']}
                for s in sonos_discovery()
            ]
        else:
            kind = 'audio devices'
            devices = [
                {'name': d['name'], 'id': d['device'], 'detail': d['device']}
                for d in mpv_discovery()
            ]
    except JukeboxUnavailableException as e:
        return flask.jsonify({'ok': False, 'message': str(e), 'devices': []})

    if not devices:
        return flask.jsonify({'ok': False, 'message': f'No {kind} found.', 'devices': []})

    plur = 's' if len(devices) > 1 else ''
    return flask.jsonify({'ok': True, 'message': f'Found {len(devices)} device{plur}.', 'devices': devices})
