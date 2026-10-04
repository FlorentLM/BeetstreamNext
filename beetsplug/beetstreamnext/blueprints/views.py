from __future__ import annotations

from typing import Optional
import flask

from beetsplug.beetstreamnext.core.media.shares import list_shares
from beetsplug.beetstreamnext.core.services.radio import list_radios
from beetsplug.beetstreamnext.core.services.external.radio_browser import query_radio_browser


# Web UI partials and responses shared by admin and non-admin account pages


def modal_error(message: str, result_id: str) -> flask.Response:
    """Error for a form inside a modal: retargets the response from the list to the modal's message slot."""
    response = flask.make_response(flask.render_template('partials/action_result.html', message=message, ok=False))
    response.headers['HX-Retarget'] = f'#{result_id}'
    response.headers['HX-Reswap'] = 'innerHTML'
    return response


##
# Shares

def render_shares(username: Optional[str] = None) -> str:
    """Shares table: every share, or only `username`'s."""
    admin = username is None

    return flask.render_template('partials/shares_table.html', shares=list_shares(username), admin=admin,
                                 delete_endpoint='admin.route_delete_share' if admin else 'account.route_delete_my_share')


##
# Radios

def render_radios(message: Optional[str] = None) -> str:
    return flask.render_template('partials/radio_list.html', radios=list_radios(), message=message, ok=True)


def render_radio_discovery(query: Optional[str]) -> str:

    def result(stations: list, message: Optional[str]) -> str:
        return flask.render_template('partials/radio_search.html', stations=stations, message=message)

    if not flask.current_app.config.get('enable_radio_discovery'):
        return result([], 'Radio discovery is disabled.')

    query = (query or '').strip()
    if not query:
        return result([], 'Enter a station name to search.')

    stations = query_radio_browser(query, limit=15)
    if not stations:
        return result([], 'No stations found.')

    return result([
        {
            'name': s['name'],
            'stream_url': s['stream_url'],
            'homepage_url': s['homepage_url'],
            'favicon': s.get('favicon') or '',
        }
        for s in stations
    ], None)


##
# Podcasts


def render_channels(scope: str, username: Optional[str] = None, message: Optional[str] = None,
                    ok: bool = True, notices: Optional[list] = None) -> str:

    pm = flask.current_app.config['podcast_manager']
    channels, total_size = pm.channels_overview(username if scope == 'account' else None)

    return flask.render_template('partials/podcast_channels.html', scope=scope, channels=channels,
                                 total_size=total_size, message=message, ok=ok, notices=notices)


def render_episodes(scope: str, channel_id: int, username: Optional[str] = None) -> str:

    pm = flask.current_app.config['podcast_manager']
    episodes = pm.episodes_of(channel_id, username if scope == 'account' else None)

    return flask.render_template('partials/podcast_episodes.html', scope=scope, episodes=episodes)


def render_discovery(query: Optional[str]) -> str:

    pm = flask.current_app.config['podcast_manager']
    feeds, message = pm.discover(query)

    return flask.render_template('partials/podcast_search.html', feeds=feeds, message=message)


def download_recents_message(count: int) -> str:
    if count:
        return f"Downloading {count} recent episode{'s' if count != 1 else ''}."

    return ("No episodes to download (already downloaded/downloading, or "
            "'podcast_auto_download_count' is set to 0).")


def delete_downloads_message(count: int) -> str:
    if count:
        return f"Deleted {count} downloaded episode{'s' if count != 1 else ''}."

    return 'No downloaded episodes to delete.'


def opml_response(username: Optional[str] = None) -> flask.Response:
    """OPML export as a file download: every channel, or only `username`'s subscriptions."""
    pm = flask.current_app.config['podcast_manager']

    return flask.Response(
        pm.export_opml(username),
        mimetype='text/x-opml+xml',
        headers={'Content-Disposition': 'attachment; filename="BeetstreamNext-Podcasts.opml"'},
    )
