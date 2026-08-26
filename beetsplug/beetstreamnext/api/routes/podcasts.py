from typing import List
import flask

from .. import api_bp

from beetsplug.beetstreamnext.constants import FEEDPARSER
from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.utils.general import api_bool
from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.api.responses import subsonic_response, subsonic_error
from beetsplug.beetstreamnext.api.serializers import IDMapper, map_podcast_channel, map_podcast_episode


def _list_channels() -> List[dict]:

    with database() as db:
        rows = db.execute(
            """
            SELECT *
            FROM podcast_channels
            ORDER BY title COLLATE NOCASE
            """
        ).fetchall()

    return [dict(r) for r in rows]


def _list_episodes(channel_id: int) -> List[dict]:

    with database() as db:
        rows = db.execute(
            """
            SELECT * FROM podcast_episodes 
            WHERE channel_id = ? 
            ORDER BY publish_date DESC
            """, (channel_id,)
        ).fetchall()

    return [dict(r) for r in rows]


def _newest_episodes(count: int) -> List[dict]:

    with database() as db:
        rows = db.execute(
            """
            SELECT pe.*, pc.title AS channel_title
            FROM podcast_episodes pe
            JOIN podcast_channels pc ON pc.id = pe.channel_id
            WHERE pe.publish_date IS NOT NULL
            ORDER BY pe.publish_date DESC
            LIMIT ?
            """, (count,)
        ).fetchall()

    return [dict(r) for r in rows]


##
# Endpoints


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getpodcasts/
@api_bp.route('/getPodcasts', methods=['GET', 'POST'])
@api_bp.route('/getPodcasts.view', methods=['GET', 'POST'])
def endpoint_get_podcasts() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)
    include_episodes = r.get('includeEpisodes', default=True, type=api_bool)

    if req_id:
        channel = IDMapper.resolve_podcast_channel(req_id)
        if not channel:
            return subsonic_error(70, resp_fmt=resp_fmt)
        channels = [channel]

    else:
        channels = _list_channels()

    entries = [
        map_podcast_channel(ch, _list_episodes(ch['id']) if include_episodes else None)
        for ch in channels
    ]

    payload = {
        'podcasts': {
            'channel': entries
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/getnewestpodcasts/
@api_bp.route('/getNewestPodcasts', methods=['GET', 'POST'])
@api_bp.route('/getNewestPodcasts.view', methods=['GET', 'POST'])
def endpoint_get_newest_podcasts() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    count = r.get('count', default=20, type=int)

    entries = [
        map_podcast_episode(row, {'channel_title': row.get('channel_title')})
        for row in _newest_episodes(count)
    ]

    payload = {
        'newestPodcasts': {
            'episode': entries
        }
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/createpodcastchannel/
@api_bp.route('/createPodcastChannel', methods=['GET', 'POST'])
@api_bp.route('/createPodcastChannel.view', methods=['GET', 'POST'])
def endpoint_create_podcast_channel() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    url = r.get('url', default='', type=str)                # Required

    if not flask.g.user_data.get('podcastRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not FEEDPARSER:
        return subsonic_error(0,
                              message="Podcast feeds need the 'feedparser' package to be installed on the server.",
                              resp_fmt=resp_fmt)

    if not url:
        return subsonic_error(10, resp_fmt=resp_fmt)

    podcast_manager = flask.g.podcast_manager
    podcast_manager.create_channel(url)

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/deletepodcastchannel/
@api_bp.route('/deletePodcastChannel', methods=['GET', 'POST'])
@api_bp.route('/deletePodcastChannel.view', methods=['GET', 'POST'])
def endpoint_delete_podcast_channel() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)         # Required

    if not flask.g.user_data.get('podcastRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not req_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    channel = IDMapper.resolve_podcast_channel(req_id)
    if not channel:
        return subsonic_error(70, resp_fmt=resp_fmt)

    podcast_manager = flask.g.podcast_manager
    podcast_manager.delete_channel(channel['id'])

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/refreshpodcasts/
@api_bp.route('/refreshPodcasts', methods=['GET', 'POST'])
@api_bp.route('/refreshPodcasts.view', methods=['GET', 'POST'])
def endpoint_refresh_podcasts() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    if not flask.g.user_data.get('podcastRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not FEEDPARSER:
        return subsonic_error(0,
                              message="Podcasts need the 'feedparser' package to be installed on the server.",
                              resp_fmt=resp_fmt)

    podcast_manager = flask.g.podcast_manager
    podcast_manager.background_refresh()

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/downloadpodcastepisode/
@api_bp.route('/downloadPodcastEpisode', methods=['GET', 'POST'])
@api_bp.route('/downloadPodcastEpisode.view', methods=['GET', 'POST'])
def endpoint_download_podcast_episode() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)         # Required

    if not flask.g.user_data.get('podcastRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not req_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    episode = IDMapper.resolve_podcast_episode(req_id)
    if not episode:
        return subsonic_error(70, resp_fmt=resp_fmt)

    if episode.get('status') == 'completed':
        return subsonic_response({}, resp_fmt=resp_fmt)   # already on disk, nothing to do here

    podcast_manager = flask.g.podcast_manager
    has_started = podcast_manager.background_download(episode['id'])

    if not has_started:
        # Couldn't start (no audio source or whatever): don't tell the client to wait for a download
        return subsonic_error(0, message='This episode has no audio source and/or cannot be downloaded.', resp_fmt=resp_fmt)

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/endpoints/deletepodcastepisode/
@api_bp.route('/deletePodcastEpisode', methods=['GET', 'POST'])
@api_bp.route('/deletePodcastEpisode.view', methods=['GET', 'POST'])
def endpoint_delete_podcast_episode() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)         # Required

    if not flask.g.user_data.get('podcastRole'):
        return subsonic_error(50, resp_fmt=resp_fmt)

    if not req_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    episode = IDMapper.resolve_podcast_episode(req_id)
    if not episode:
        return subsonic_error(70, resp_fmt=resp_fmt)

    podcast_manager = flask.g.podcast_manager
    podcast_manager.delete_episode(episode['id'])

    return subsonic_response({}, resp_fmt=resp_fmt)


# Spec: https://opensubsonic.netlify.app/docs/extensions/getpodcastepisode/
@api_bp.route('/getPodcastEpisode', methods=['GET', 'POST'])
@api_bp.route('/getPodcastEpisode.view', methods=['GET', 'POST'])
def endpoint_get_podcast_episode() -> flask.Response:
    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)
    req_id = r.get('id', default='', type=safe_str)         # Required

    if not req_id:
        return subsonic_error(10, resp_fmt=resp_fmt)

    episode = IDMapper.resolve_podcast_episode(req_id)
    if not episode:
        return subsonic_error(70, resp_fmt=resp_fmt)

    channel = IDMapper.resolve_podcast_channel(IDMapper.mint_podcast_channel(episode['channel_id']))

    payload = {
        'podcastEpisode': map_podcast_episode(episode, channel)
    }
    return subsonic_response(payload, resp_fmt=resp_fmt)
