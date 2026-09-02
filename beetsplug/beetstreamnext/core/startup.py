import logging
from pathlib import Path
from typing import Iterable, List, Optional
from flask_cors import CORS
from waitress import serve
from werkzeug.middleware.proxy_fix import ProxyFix

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.console import TermColors, print_box
from beetsplug.beetstreamnext.constants import CACHE_LOCATION, LOOPBACK_IPS
from beetsplug.beetstreamnext.core.database import ensure_secret, initialise_db, rotate_session_key
from beetsplug.beetstreamnext.core.logging import LOG_LEVEL, RedactingTransLogger, apply_logs_redaction, bsn_logger
from beetsplug.beetstreamnext.core.playlists import PlaylistProvider
from beetsplug.beetstreamnext.core.podcasts import PodcastManager
from beetsplug.beetstreamnext.core.security import ip_filter
from beetsplug.beetstreamnext.settings import settings_store


def prestartup_config(
        beets_db_path: str | Path,
        bsn_db_path: str | Path,
        beets_config_path: Optional[str | Path],
        ip_whitelist: Iterable[str] = (),
        ip_blacklist: Iterable[str] = (),
        standalone: bool = False,
    ) -> None:
    """
    Setup configs and secrets needed before anything else touches the db
    """

    app.config.update(
        BEETS_DB_PATH=Path(beets_db_path),
        BSN_DB_PATH=Path(bsn_db_path),
        BEETS_CONFIG_PATH=beets_config_path,
        STANDALONE_MODE=standalone,
    )

    ip_filter.whitelist = list(ip_whitelist)
    ip_filter.blacklist = list(ip_blacklist)

    ensure_secret(bsn_db_path)
    app.config.update(SECRET_KEY=rotate_session_key(CACHE_LOCATION))


def run_server(
        lib,
        host: List[str],
        port: int,
        debug: bool,
        force_trust_host: bool,
        root_directory: Path,
        playlist_dirs: dict,
        yaml_defaults: dict,
    ) -> None:
    """
    Start BeetsreamNext :)))
    """

    app.config['HOST_LIST'] = host  # WebUI uses them as external_hostname suggestions

    with app.app_context():
        initialise_db()
        # Read db, merge with yaml_defaults, populate the cache, and trigger all LIVE_APPLY_SETTING
        settings_store.initialise(yaml_defaults)

    if debug and any(h not in LOOPBACK_IPS for h in host):
        if force_trust_host:
            print_box([
                '',
                f'{TermColors.WARNING + TermColors.BOLD + TermColors.REVERSE}  !!! SUPER IMPORTANT WARNING !!!  {TermColors.ENDC}',
                '',
                f"Debug mode is force-enabled on {', '.join(host)}.",
                'The Werkzeug debugger allows arbitrary remote code execution.',
                '',
                "I hope you know what you're doing!",
                '',
            ], color=TermColors.WARNING)

        else:
            print_box([
                '',
                f'{TermColors.FAIL + TermColors.BOLD + TermColors.REVERSE}  STARTUP ABORTED:  {TermColors.ENDC}',
                '',
                'Debug mode can only be used on localhost.',
                'The Werkzeug debugger allows arbitrary remote code execution.',
                '',
            ], color=TermColors.FAIL)
            return

    if settings_store.get('legacy_auth') and not settings_store.get('reverse_proxy'):
        if any(h not in LOOPBACK_IPS for h in host):
            print_box([
                '',
                f'{TermColors.WARNING + TermColors.BOLD + TermColors.REVERSE}  SECURITY WARNING:  {TermColors.ENDC}',
                '',
                'Legacy authentication is enabled, and the server',
                f"is listening on {', '.join(f'http://{h}:{port}' for h in host)}",
                'without a reverse proxy.',
                '',
                'Passwords from legacy clients may be',
                'transmitted in cleartext over HTTP.',
                '',
            ], color=TermColors.WARNING)

    if settings_store.get('reverse_proxy'):
        if any(h not in LOOPBACK_IPS for h in host):
            print_box([
                '',
                f'{TermColors.WARNING + TermColors.BOLD + TermColors.REVERSE}  SECURITY WARNING:  {TermColors.ENDC}',
                '',
                'reverse_proxy is enabled and the server is bound to',
                f"{', '.join(host)}:{port} (not loopback).",
                '',
                'Make sure this address is *not* reachable without going through the proxy.',
                '',
                'Bind to 127.0.0.1 (or a unix socket), unless a firewall',
                'guarantees only the proxy can reach this port.',
                '',
            ], color=TermColors.WARNING)

        # Trusting 'proxy_hops' number of forwarded entries
        hops = max(1, settings_store.get('proxy_hops'))
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=hops, x_proto=hops, x_host=hops, x_port=hops, x_prefix=hops,
        )
        app.config.update(SESSION_COOKIE_SECURE=True)

    # App-level things that don't belong in db settings
    app.config.update(
        lib=lib,
        root_directory=root_directory,
        playlist_dirs=playlist_dirs
    )

    app.config.update(playlist_provider=PlaylistProvider())
    app.config.update(podcast_manager=PodcastManager())

    # Handle "requires restart" settings
    cors_origin = settings_store.get('cors_origins')
    supports_creds = settings_store.get('cors_supports_credentials')

    # Enable CORS if required
    if cors_origin:
        if cors_origin == '*' and supports_creds:
            print_box([
                '',
                f'{TermColors.WARNING + TermColors.BOLD + TermColors.REVERSE}  SECURITY WARNING:  {TermColors.ENDC}',
                '',
                "CORS is set to allow all origins ('*') WITH credentials.",
                'This could allow any malicious website you visit to silently interact',
                'with your BeetstreamNext server in the background.',
                '',
                "It is highly recommended to only allow your specific player's URL.",
                ''
            ], color=TermColors.WARNING)
        else:
            bsn_logger.info(f'Enabling CORS for origin(s): {cors_origin}')

        origins_list = [o.strip() for o in cors_origin.split(',')] if ',' in cors_origin else cors_origin
        app.config.update(
            CORS_ALLOW_HEADERS='Content-Type',
            CORS_RESOURCES={r"/*": {"origins": origins_list}}
        )
        CORS(app, supports_credentials=supports_creds)

    else:
        bsn_logger.info('CORS is disabled (secure default). Web-based clients will be blocked by browsers.')

    apply_logs_redaction()
    if debug:
        if len(host) > 1:
            bsn_logger.warning(
                f"Debug mode (Werkzeug) can only bind one interface, ignoring all but '{host[0]}' "
                f"(configured: {', '.join(host)})."
            )
        app.run(host=host[0], port=port, debug=True, threaded=True)

    else:
        logging.getLogger('waitress').setLevel(LOG_LEVEL)
        threads = settings_store.get('threads')
        channel_timeout = settings_store.get('channel_timeout')
        connection_limit = settings_store.get('connection_limit')
        logging.getLogger('waitress').setLevel(LOG_LEVEL)

        if LOG_LEVEL > logging.INFO:
            urls = ', '.join(f'http://{h}:{port}' for h in host)
            print(f'BeetstreamNext server running on {urls}...')

        logged_app = RedactingTransLogger(app, setup_console_handler=True)

        serve(
            logged_app, listen=[f'{h}:{port}' for h in host], threads=threads,
            channel_timeout=channel_timeout, connection_limit=connection_limit
        )
