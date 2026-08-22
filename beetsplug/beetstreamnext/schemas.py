from typing import TypedDict, Any, Callable, Dict

from beetsplug.beetstreamnext.core.security import ip_filter, validate_trusted_hosts


## Allowed image formats

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.tiff', '.tif', '.webp', '.bmp'}


## Allowed bitrates and sizes

ALLOWED_BITRATES = frozenset({0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320})
ALLOWED_THUMBNAIL_SIZES = [56, 120, 250, 500, 1000, 1200]

BITRATE_CHOICES_STR = [(0, 'No limit')] + [(b, f'{b} kbps') for b in sorted(ALLOWED_BITRATES) if b > 0]


## User data fields and roles

USER_ROLES_SCHEMA = (
    # name,                 label,              default
    ('adminRole',           'Admin',            False),
    ('settingsRole',        'Settings',         True),
    ('streamRole',          'Stream',           True),
    ('downloadRole',        'Download',         False),
    ('uploadRole',          'Upload',           False),
    ('playlistRole',        'Playlists',        True),
    ('commentRole',         'Comments',         True),
    ('coverArtRole',        'Cover art',        False),
    ('podcastRole',         'Podcasts',         False),
    ('shareRole',           'Sharing',          False),
    ('jukeboxRole',         'Jukebox',          False),
    ('videoConversionRole', 'Video conversion', False),
    ('scrobblingEnabled',   'Scrobbling',       True),
)

_ROLE_NAMES = {role[0] for role in USER_ROLES_SCHEMA}

ALL_USER_FIELDS = frozenset({
    'username', 'password', 'email', 'avatar', 'avatarLastChanged',
    'folder', 'maxBitRate'
} | _ROLE_NAMES)

PRIVATE_USER_FIELDS = frozenset({'password', 'avatar', 'api_key_hash'})
PUBLIC_USER_FIELDS = ALL_USER_FIELDS - PRIVATE_USER_FIELDS


## Settings schemas

class SettingDescriptor(TypedDict, total=False):
    type: str                           # 'bool' | 'int' | 'str' | 'list[str]'
    default: Any
    category: str
    description: str
    requires_restart: bool
    sensitive: bool                     # Encrypt at rest, hide from logs, etc
    validator: Callable[[Any], Any]     # Raise ValueError on bad input


def _int_range(lo: int, hi: int) -> Callable[[Any], int]:
    def _v(x: Any) -> int:
        n = int(x)
        if not lo <= n <= hi:
            raise ValueError(f'Must be between {lo} and {hi}')
        return n
    return _v


SETTINGS_SCHEMA: Dict[str, SettingDescriptor] = {

    # Server / network
    'admin_hostname': {
        'type': 'str',
        'default': '',
        'category': 'server',
        'description': 'If set, the admin panel will only be accessible when visited via this hostname (e.g. https://beetstreamnext.internal.example.com). Loopback is always allowed.',
        'requires_restart': False,
    },
    'external_hostname': {
        'type': 'str',
        'default': '',
        'category': 'server',
        'description': 'Your external, public hostname (e.g. https://music.example.com).',
        'requires_restart': False,
    },
    'threads': {
        'type': 'int',
        'default': 16,
        'category': 'server',
        'description': 'Worker threads for serving requests.',
        'requires_restart': True,
        'validator': _int_range(1, 128),
    },
    'channel_timeout': {
        'type': 'int',
        'default': 120,
        'category': 'server',
        'description': 'Seconds of inactivity allowed on a connection before Waitress closes it. Lower this on low-resource environments to free up connections faster.',
        'requires_restart': True,
        'validator': _int_range(1, 3600),
    },
    'connection_limit': {
        'type': 'int',
        'default': 100,
        'category': 'server',
        'description': 'Maximum number of simultaneous connections Waitress will accept. Lower this on low-resource environments to cap memory/socket usage.',
        'requires_restart': True,
        'validator': _int_range(1, 10000),
    },
    'cors_origins': {
        'type': 'str',
        'default': '',
        'category': 'server',
        'description': "Allowed CORS origins (comma-separated, '*' for all). Empty to disable CORS.",
        'requires_restart': True,
    },
    'cors_supports_credentials': {
        'type': 'bool',
        'default': False,
        'category': 'server',
        'description': 'Allow CORS requests with credentials (cookies, HTTP auth).',
        'requires_restart': True,
    },
    'reverse_proxy': {
        'type': 'bool',
        'default': False,
        'category': 'server',
        'description': 'Server is behind a reverse proxy (Nginx, Caddy, Traefik, etc.).',
        'requires_restart': True,
    },
    'proxy_hops': {
        'type': 'int',
        'default': 1,
        'category': 'server',
        'description': 'Number of trusted reverse proxies in front of the server. Only used if `reverse_proxy` is enabled',
        'requires_restart': True,
        'validator': _int_range(1, 10),
    },
    'trusted_hosts': {
        'type': 'str',
        'default': '',
        'category': 'server',
        'description': 'Allowed Host headers (domain names/IPs, comma-separated). If empty, all hosts are allowed. Loopback is always allowed.',
        'requires_restart': False,
        'validator': validate_trusted_hosts,
    },
    'legacy_auth': {
        'type': 'bool',
        'default': True,
        'category': 'server',
        'description': 'Allow legacy MD5 token / cleartext password authentication. API-key authentication always works.',
        'requires_restart': False,
    },
    'public_now_playing': {
        'type': 'bool',
        'default': False,
        'category': 'server',
        'description': 'Show the currently playing song on the public home page.',
        'requires_restart': False,
    },

    # Library
    'never_transcode': {
        'type': 'bool',
        'default': False,
        'category': 'library',
        'description': 'Never transcode files, always stream the original.',
        'requires_restart': False,
    },
    'lastfm_api_key': {
        'type': 'str',
        'default': '',
        'category': 'library',
        'description': 'Last.fm API key for fetching metadata.',
        'requires_restart': False,
        'sensitive': True,
    },
    'fetch_artists_images': {
        'type': 'bool',
        'default': False,
        'category': 'library',
        'description': 'Fetch missing artist images from external services.',
        'requires_restart': False,
    },
    'save_artists_images': {
        'type': 'bool',
        'default': False,
        'category': 'library',
        'description': 'Save fetched artist images to disk.',
        'requires_restart': False,
    },
    'fetch_artists_biographies': {
        'type': 'bool',
        'default': False,
        'category': 'library',
        'description': 'Fetch artist short biography from Wikipedia.',
        'requires_restart': False,
    },
    'save_album_art': {
        'type': 'bool',
        'default': False,
        'category': 'library',
        'description': 'Save fetched album art alongside music files.',
        'requires_restart': False,
    },
    'fetch_lyrics': {
        'type': 'bool',
        'default': False,
        'category': 'library',
        'description': "Fetch missing song lyrics using Beets' Lyrics plugin.",
        'requires_restart': False,
    },
    'save_lyrics': {
        'type': 'bool',
        'default': False,
        'category': 'library',
        'description': 'Save fetched lyrics to the beets library database.',
        'requires_restart': False,
    },
    'fetch_album_version': {
        'type': 'bool',
        'default': False,
        'category': 'library',
        'description': "Fetch album version info ('Deluxe Edition', 'Japanese Expanded Edition', etc.) from MusicBrainz.",
        'requires_restart': False,
    },
    'save_album_version': {
        'type': 'bool',
        'default': False,
        'category': 'library',
        'description': 'Save fetched album version info to the beets database.',
        'requires_restart': False,
    },
    'ratings_writeback_user': {
        'type': 'str',
        'default': '',
        'category': 'library',
        'description': (
            "Commit this user's Likes and Ratings into the Beets library so they survive outside "
            "BeetstreamNext. Beets has no concept of per-user data, so only one user's changes can "
            "be committed this way. Leave unset to disable."
        ),
        'requires_restart': False,
    },
    'enable_radio_discovery': {
        'type': 'bool',
        'default': False,
        'category': 'library',
        'description': 'Enable Radio Browser API for station discovery.',
        'requires_restart': False,
    },
    # 'fetch_radio_images': {
    #     'type': 'bool',
    #     'default': True,
    #     'category': 'library',
    #     'description': 'Automatically fetch station icons when adding from Radio Browser.',
    #     'requires_restart': False,
    # },

    # Audio
    'replaygain_enabled': {
        'type': 'bool',
        'default': False,
        'category': 'audio',
        'description': 'Apply ReplayGain normalization on the server side.',
        'requires_restart': False,
    },
    'replaygain_preamp': {
        'type': 'int',
        'default': 0,
        'category': 'audio',
        'description': 'Additional gain (dB) to apply.',
        'requires_restart': False,
        'validator': _int_range(-20, 20),
    },
    'replaygain_fallback': {
        'type': 'int',
        'default': -6,
        'category': 'audio',
        'description': "Gain (dB) to apply to tracks without ReplayGain tags in beets' library.",
        'requires_restart': False,
        'validator': _int_range(-20, 0),
    },
    'audio_peak_limit': {
        'type': 'bool',
        'default': False,
        'category': 'audio',
        'description': 'Always prevent audio peaks from exceeding 0 dB (prevent clipping).',
        'requires_restart': False,
    },

    # Security
    'ip_whitelist': {
        'type': 'list[str]',
        'default': [],
        'category': 'security',
        'description': 'Allowed IPs (empty = allow all except blacklist).',
        'requires_restart': False,
        'validator': ip_filter.parse_ips,
    },
    'ip_blacklist': {
        'type': 'list[str]',
        'default': [],
        'category': 'security',
        'description': 'Banned IPs.',
        'requires_restart': False,
        'validator': ip_filter.parse_ips,
    },
    'rate_limit_max_failures': {
        'type': 'int',
        'default': 5,
        'category': 'security',
        'description': 'Failed attempts before an IP is rate-limited.',
        'requires_restart': False,
        'validator': _int_range(1, 100),
    },
    'rate_limit_block_window': {
        'type': 'int',
        'default': 300,
        'category': 'security',
        'description': 'Seconds before failures roll off.',
        'requires_restart': False,
        'validator': _int_range(10, 86400),
    },
    'rate_limit_ip_max_failures': {
        'type': 'int',
        'default': 20,
        'category': 'security',
        'description': 'Failed attempts from a single IP (across any usernames tried) before that IP is blocked outright. Catches attackers rotating usernames to dodge the per-user limit above.',
        'requires_restart': False,
        'validator': _int_range(1, 1000),
    },
    'rate_limit_ip_block_window': {
        'type': 'int',
        'default': 3600,
        'category': 'security',
        'description': 'Seconds before an IP-wide failure count rolls off.',
        'requires_restart': False,
        'validator': _int_range(10, 604800),
    },
}

SETTINGS_CATEGORIES = frozenset(s.get('category') for s in SETTINGS_SCHEMA.values())
