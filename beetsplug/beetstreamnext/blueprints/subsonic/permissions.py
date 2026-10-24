from __future__ import annotations

from beetsplug.beetstreamnext.core.accounts.app_passwords import AppPasswordRole


# Endpoints that change state, blocked for readonly app passwords
WRITE_ENDPOINTS = frozenset({
    'createUser', 'updateUser', 'deleteUser', 'changePassword',
    'addChatMessage',
    'createBookmark', 'deleteBookmark',
    'createPlaylist', 'updatePlaylist', 'deletePlaylist',
    'createShare', 'updateShare', 'deleteShare',
    'createInternetRadioStation', 'updateInternetRadioStation', 'deleteInternetRadioStation',
    'createPodcastChannel', 'deletePodcastChannel', 'deletePodcastEpisode',
    'downloadPodcastEpisode', 'refreshPodcasts',
    'savePlayQueue', 'savePlayQueueByIndex',
    'scrobble', 'reportPlayback',
    'setRating', 'star', 'unstar',
    'startScan', 'jukeboxControl',
})
# TODO: this may be too restrictive...


def app_role_allows(role: AppPasswordRole, path: str) -> bool:
    """
    Whether a Subsonic endpoint can be accessed by an app password with this role.
    """

    name = path.rstrip('/').rsplit('/', 1)[-1].removesuffix('.view')

    if role == AppPasswordRole.READONLY and name in WRITE_ENDPOINTS:
        return False

    return True
