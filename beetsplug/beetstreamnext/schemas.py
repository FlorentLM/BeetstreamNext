import shutil
from typing import TypedDict, Any, Callable, Dict, Tuple

from beetsplug.beetstreamnext.constants import SERVER_NAME
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
    choices: Tuple[str, ...]            # If set, admin UI renders a <select> instead of free text
    help: str                           # If set, admin UI shows this as a dotted box


def _int_range(lo: int, hi: int) -> Callable[[Any], int]:
    def _v(x: Any) -> int:
        n = int(x)
        if not lo <= n <= hi:
            raise ValueError(f'Must be between {lo} and {hi}')
        return n
    return _v


def _choice(*options: str) -> Callable[[Any], str]:
    def _v(x: Any) -> str:
        s = str(x)
        if s not in options:
            raise ValueError(f"Must be one of: {', '.join(options)}")
        return s
    return _v


def _validate_ffmpeg_path(x: Any) -> str:
    s = str(x or '').strip()
    if s and shutil.which(s) is None:
        raise ValueError('Not an executable file (or not found).')
    return s


SETTINGS_SCHEMA: Dict[str, SettingDescriptor] = {

    # Server / network
    'admin_hostname': {
        'type': 'str',
        'default': '',
        'category': 'server',
        'description': f'If set, the admin panel will only be accessible when visited via this hostname '
                       f'(e.g. https://{SERVER_NAME.lower()}.internal.example.com). Loopback is always allowed.',
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
        'help': (
            f"# Example Nginx configuration (adjust to match your {SERVER_NAME} host/port):\n"
            "\n"
            "location / {\n"
            "    proxy_pass http://127.0.0.1:8080;\n"
            "    proxy_set_header Host $host;\n"
            "    proxy_set_header X-Real-IP $remote_addr;\n"
            "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            "    proxy_set_header X-Forwarded-Proto $scheme;\n"
            "    proxy_set_header X-Forwarded-Host $host;\n"
            "    proxy_set_header X-Forwarded-Port $server_port;\n"
            "}"
        ),
    },
    'proxy_hops': {
        'type': 'int',
        'default': 1,
        'category': 'server',
        'description': 'Number of trusted reverse proxies in front of the server. Only used if `reverse_proxy` is enabled',
        'requires_restart': True,
        'validator': _int_range(1, 10),
    },
    'sendfile_method': {
        'type': 'str',
        'default': 'off',
        'category': 'server',
        'description': (
            "Offload direct (non-transcoded) file serving to the reverse proxy instead of streaming bytes "
            "through Python. 'x-accel-redirect' for Nginx, 'x-sendfile' for Apache. Only takes effect when "
            "'reverse_proxy' is enabled and the proxy is configured to honor the header."
        ),
        'requires_restart': False,
        'choices': ('off', 'x-accel-redirect', 'x-sendfile'),
        'validator': _choice('off', 'x-accel-redirect', 'x-sendfile'),
        'help': (
            "# Apache mod_xsendfile example (allow the absolute music root path):\n"
            "\n"
            "XSendFile On\n"
            "XSendFilePath /path/to/your/music/\n"
        ),
    },
    'sendfile_internal_prefix': {
        'type': 'str',
        'default': '/_bsn_internal',
        'category': 'server',
        'description': (
            "Internal URI prefix your Nginx config maps, via an internal-only 'location' block, to the music "
            "root directory. Only used when sendfile_method is 'x-accel-redirect'."
        ),
        'requires_restart': False,
        'help': (
            "# Example Nginx configuration:\n"
            "\n"
            "location /_bsn_internal/ {\n"
            "    internal;\n"
            "    alias /path/to/your/music/;\n"
            "}"
        ),
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
    'follow_playlist_embedded_urls': {
        'type': 'bool',
        'default': False,
        'category': 'library',
        'description': (
            "Fetch external images from foreign playlists' #EXTALBUMARTURL lines (for albums that "
            "have no art locally). Only enable this if you trust the source of your imported playlists."
        ),
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
    'discogs_ratings': {
        'type': 'str',
        'default': 'off',
        'category': 'library',
        'description': (
            "Use Discogs' public community rating for an album's averageRating. `fallback`: Only "
            "use Discogs when nobody on this server has rated the album locally. It never overrides a "
            "local rating. `prefer`: Always uses Discogs when available (falling back "
            "to the local average when it isn't)."
        ),
        'requires_restart': False,
        'choices': ('off', 'fallback', 'prefer'),
        'validator': _choice('off', 'fallback', 'prefer'),
    },
    'ignored_articles': {
        'type': 'str',
        'default': (
            "The A An Der Die Das Ein Eine El La Los Las Un Una Le Les Il Lo Gli Uno O Os As Um Uma De Het Den Det"
        ),
        'category': 'library',
        'description': (
            'Space-separated articles (across any language) to ignore when sorting '
            "artists alphabetically (for instance, 'The Beatles' -> B). "
        ),
        'requires_restart': False,
    },
    'ratings_writeback_user': {
        'type': 'str',
        'default': '',
        'category': 'library',
        'description': (
            "Commit this user's Likes and Ratings into the Beets library so they survive outside "
            f"{SERVER_NAME}. Beets has no concept of per-user data, so only one user's changes can "
            "be committed this way. Leave unset to disable."
        ),
        'requires_restart': False,
    },
    'external_playlists_editors': {
        'type': 'list[str]',
        'default': [],
        'category': 'library',
        'description': (
            f"Who can rename/edit/delete non{SERVER_NAME} playlists "
            "(from Beets' `playlist` plugin directory)."
            "Smartplaylist-generated playlists are always read-only."
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
    'audiomuse_api_token': {
        'type': 'str',
        'default': '',
        'category': 'library',
        'description': 'API token for your AudioMuse-AI instance.',
        'requires_restart': False,
        'sensitive': True,
    },
    'audiomuse_url': {
        'type': 'str',
        'default': '',
        'category': 'library',
        'description': 'URL to your AudioMuse-AI instance (e.g. http://localhost:8000) to enable sonic similarity endpoints.',
        'requires_restart': False,
    },

    # Podcasts
    'podcast_storage_dir': {
        'type': 'str',
        'default': '',
        'category': 'podcasts',
        'description': (
            "Directory to store downloaded podcast episode audio. Leave empty to use the "
            "default cache location."
        ),
        'requires_restart': False,
    },
    'podcast_auto_download_count': {
        'type': 'int',
        'default': 10,
        'category': 'podcasts',
        'description': (
            "Number of episodes to download of a channel's most recent episodes when "
            "added. Set to 0 to disable and only download episodes on request."
        ),
        'requires_restart': False,
        'validator': _int_range(0, 200),
    },

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
    'ffmpeg_path': {
        'type': 'str',
        'default': '',
        'category': 'audio',
        'description': (
            "Path to the ffmpeg binary, if it isn't on the system PATH (e.g. /usr/local/bin/ffmpeg). "
            "Leave empty to auto-detect from PATH."
        ),
        'requires_restart': False,
        'validator': _validate_ffmpeg_path,
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

SETTINGS_CATEGORIES = tuple(dict.fromkeys(s.get('category') for s in SETTINGS_SCHEMA.values()))
