from __future__ import annotations

from beetsplug.beetstreamnext.constants import BITRATE_CHOICES_STR


def allowed_bitrates(limit: int) -> list[tuple[int, str]]:
    """Bitrate choices at or below `limit` for users picking their own cap."""
    if not limit:
        return list(BITRATE_CHOICES_STR)
    return [(b, label) for b, label in BITRATE_CHOICES_STR if 0 < b <= limit]


def effective_bitrate(user: dict) -> int:
    """Streaming limit in kbps (0 = none): admin cap or user's preference."""
    limits = (int(user.get('maxBitRate') or 0), int(user.get('bitRatePreference') or 0))
    return min((b for b in limits if b > 0), default=0)


USER_ROLES_SCHEMA = (
    # name,                 label,              default
    ('adminRole',           'Admin',            False),     # Whether the user is administrator
    ('settingsRole',        'Settings',         True),      # Whether the user is allowed to change personal settings and password
    ('streamRole',          'Stream',           True),      # Whether the user is allowed to play files
    ('downloadRole',        'Download',         False),     # Whether the user is allowed to download files
    ('uploadRole',          'Upload',           False),     # Whether the user is allowed to upload files
    ('playlistRole',        'Playlists',        True),      # Whether the user is allowed to create and delete playlists
    ('commentRole',         'Comments',         True),      # Whether the user is allowed to create and edit comments and ratings
    ('coverArtRole',        'Cover art',        False),     # Whether the user is allowed to change cover art and tags
    ('podcastRole',         'Podcasts',         False),     # Whether the user is allowed to administrate Podcasts (subscribe and manage their own subscriptions)
    ('shareRole',           'Sharing',          False),     # Whether the user is allowed to share files with anyone
    ('jukeboxRole',         'Jukebox',          False),     # Whether the user is allowed to play files in jukebox mode
    ('videoConversionRole', 'Video conversion', False),     # Whether the user is allowed to start video conversions
    ('scrobblingEnabled',   'Scrobbling',       True),
)

_ROLE_NAMES = {role[0] for role in USER_ROLES_SCHEMA}

ALL_USER_FIELDS = frozenset({
    'username', 'password', 'email', 'avatar', 'avatarLastChanged',
    'folder', 'maxBitRate', 'bitRatePreference', 'playHistoryEnabled'
} | _ROLE_NAMES)

PRIVATE_USER_FIELDS = frozenset({'password', 'avatar'})
PUBLIC_USER_FIELDS = ALL_USER_FIELDS - PRIVATE_USER_FIELDS


def users_table_sql(table: str = 'users') -> str:

    role_columns_sql = ",\n            ".join([
        f'{name} INTEGER DEFAULT {1 if default else 0}'
        for name, _, default in USER_ROLES_SCHEMA
    ])

    return f"""
        CREATE TABLE IF NOT EXISTS {table}
        (
            username            TEXT PRIMARY KEY,
            password            BLOB NOT NULL,      -- one-way hash
            email               TEXT,
            avatar              BLOB,
            avatarLastChanged   REAL,
            folder              INTEGER DEFAULT 0,
            maxBitRate          INTEGER DEFAULT 0,  -- admin cap. 0 = no limit, otherwise kbps: 32/40/48/56/64/80/96/112/128/160/192/224/256/320
            bitRatePreference   INTEGER DEFAULT 0,  -- user's own limit (same values), applied within maxBitRate
            playHistoryEnabled  INTEGER DEFAULT 1,  -- 0 = user opted out of history
            {role_columns_sql}
        )
        """
