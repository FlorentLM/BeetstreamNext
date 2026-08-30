import flask

from .. import api_bp

from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.api.idmapper import IDMapper

from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.core.cache import preload_songs
from beetsplug.beetstreamnext.core.external import _audiomuse_get


def _parse_audiomuse_result(tracks: list, with_distance: bool = True) -> list:
    """
    Turn AudioMuse-AI track results into subsonic song payloads.
    With AudioMuse-AI configured with this server as its OpenSubsonic source, tracks item_id are our IDs
    """
    if not tracks:
        return []

    ids_in_order = [t['item_id'] for t in tracks if t.get('item_id')]
    resolved = IDMapper.resolve_many_songs(ids_in_order)
    preload_songs(list(resolved.values()))

    matches = []
    for t in tracks:
        item = resolved.get(t.get('item_id'))
        if not item:
            continue

        match = {'entry': IDMapper.map_song(item)}
        if with_distance and t.get('distance') is not None:
            match['distance'] = round(t['distance'], 4)
        matches.append(match)

    return matches


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getSonicSimilarTracks/
@api_bp.route('/getSonicSimilarTracks', methods=['GET', 'POST'])
@api_bp.route('/getSonicSimilarTracks.view', methods=['GET', 'POST'])
def endpoint_get_sonic_similar_tracks() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    song_id = r.get('id', default='', type=safe_str)        # Required
    count = r.get('count', default=50, type=int)

    if not song_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    data, err = _audiomuse_get('/api/similar_tracks', {'item_id': song_id, 'n': count})
    if err:
        return subsonic_error(0, message=err, resp_fmt=resp_fmt)

    payload = {
        'sonicMatch': _parse_audiomuse_result(data if isinstance(data, list) else [])
    }

    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/findSonicPath/
@api_bp.route('/findSonicPath', methods=['GET', 'POST'])
@api_bp.route('/findSonicPath.view', methods=['GET', 'POST'])
def endpoint_find_sonic_path() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    start_id = r.get('startSongId', default='', type=safe_str)  # Required
    end_id = r.get('endSongId', default='', type=safe_str)      # Required
    max_steps = r.get('count', default=25, type=int)

    if not start_id or not end_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    data, err = _audiomuse_get(
        '/api/find_path',
        {'start_song_id': start_id, 'end_song_id': end_id, 'max_steps': max_steps},
        timeout=15.0
    )
    if err:
        return subsonic_error(0, message=err, resp_fmt=resp_fmt)

    tracks = (data or {}).get('path', [])
    payload = {
        'sonicMatch': _parse_audiomuse_result(tracks, with_distance=False)
    }

    return subsonic_response(payload, resp_fmt=resp_fmt)
