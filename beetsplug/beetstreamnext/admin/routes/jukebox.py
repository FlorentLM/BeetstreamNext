import flask

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.core.jukebox import sonos_discovery, JukeboxUnavailableException


@admin_bp.route('/settings/jukebox/discover-speakers', methods=['GET'])
@admin_required
def route_discover_sonos_speakers() -> flask.Response:
    try:
        speakers = sonos_discovery()
    except JukeboxUnavailableException as e:
        return flask.jsonify({'ok': False, 'message': str(e), 'speakers': []})

    if not speakers:
        return flask.jsonify({'ok': False, 'message': 'No Sonos speakers found.', 'speakers': []})

    plur = 's' if len(speakers) > 1 else ''
    return flask.jsonify({'ok': True, 'message': f'Found {len(speakers)} speaker{plur}.', 'speakers': speakers})
