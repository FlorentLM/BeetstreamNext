import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import beets
import confuse
import yaml
from beets.library import Library

from beetsplug.beetstreamnext.constants import DEFAULT_CONFIG_PATH, DEFAULT_DB_TIMEOUT
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.schemas import SETTINGS_SCHEMA
from beetsplug.beetstreamnext.settings import coerce_setting, settings_store
from beetsplug.beetstreamnext.core.startup import prestartup_config, run_server
from beetsplug.beetstreamnext.core.commands import (
    cmd_clear_cache, cmd_create_user, cmd_update_user, cmd_delete_user, cmd_list_users, cmd_change_passwd
)
from beetsplug.beetstreamnext.core.database import initialise_db
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.utils.general import api_bool
from beetsplug.beetstreamnext.utils.system import get_env


## TODO: Add this info in the readme
#
# beets_config_path:
#   --beets-config > BSN_BEETS_CONFIG > 'beets_config' in yaml > none
#
# then if beets_config_path is still not set, import stays at beets' defaults/discovery
# but if beets_config_path is set, everything else reads from it:
# 
# library_db:
#   order is: --library-db > BEETS_LIBRARY_DB > 'library_db' in yaml > beets.config['library'] (only if beets_config_path was resolved) > Error
# 
# music_root:
#   order is: --music-root > MUSIC_ROOT > 'music_root' in yaml > beets.config['directory'] (only if beets_config_path was resolved) > Error
# 
# After db is up, WebUI configurable settings still win over everything:
#   final_library_db: settings_store.get('library_path') from UI, otherwise library_db as resolved above
#   final_music_root: settings_store.get('music_root') from UI, otherwise music_root as resolved above


_USER_ARG_COMMANDS = {'update-user', 'delete-user', 'passwd'}


def _cascade_value(*values: Any, default: Any = None) -> Any:
    for v in values:
        if v is not None:
            return v
    return default


##
# Standalone entrypoint

def main(argv: Optional[List[str]] = None) -> None:

    parser = argparse.ArgumentParser(
        prog='beetstreamnext',
        description='Standalone OpenSubsonic API server for a beets music library.',
    )
    parser.add_argument(
        'command', nargs='?', default='run',
        choices=['run', 'create-user', 'update-user', 'delete-user', 'passwd', 'list-users', 'clear-cache'],
        help='Action to perform (default: run)',
    )
    parser.add_argument('username', nargs='?',
                        help='Username, for update-user/delete-user/passwd')
    parser.add_argument('--config', metavar='PATH',
                        help='YAML config file (default: /config/beetstreamnext.yaml if present)')
    parser.add_argument('--library-db', metavar='PATH',
                        help='Path to the beets library.db')            # env: BEETS_LIBRARY_DB
    parser.add_argument('--music-root', metavar='PATH',
                        help='Music root directory (where songs paths are relative to)')   # env: MUSIC_ROOT
    parser.add_argument('--bsn-db', metavar='PATH',
                        help="Path to BeetstreamNext's own db (default: alongside library.db)")     # env: BSN_DB_PATH
    parser.add_argument('--beets-config', metavar='PATH',
                        help='Optional beets confuse config file, for path formats/plugins only')   # env: BSN_BEETS_CONFIG
    parser.add_argument('--host', metavar='HOST[,HOST...]',
                        help='Host(s) to listen on, comma-separated')   # env: BSN_HOST
    parser.add_argument('--port', type=int,
                        help='Port to listen on')           # env: BSN_PORT
    parser.add_argument('--threads', type=int,
                        help='Waitress worker threads')     # env: BSN_THREADS
    parser.add_argument('--debug', action='store_true', default=None,
                        help='Run server in debug mode')
    parser.add_argument('--force-trust-host', action='store_true', default=None,
                        help='Force debug mode on non-localhost')

    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.command in _USER_ARG_COMMANDS and not args.username:
        parser.error(f"'{args.command}' requires a USERNAME argument.")

    config_path = Path(args.config) if args.config else (DEFAULT_CONFIG_PATH if DEFAULT_CONFIG_PATH.is_file() else None)

    if config_path and config_path.is_file():
        with config_path.open('r', encoding='utf-8') as f:
            yaml_cfg = yaml.safe_load(f) or {}
    else:
        yaml_cfg = {}

    # Loaded first (if passed) so its 'library' / 'directory' keys can be used as a last resort fallback
    # (BSN's own flags/env/yaml still take priority)
    beets_config_path = _cascade_value(args.beets_config, get_env('BSN_BEETS_CONFIG'), yaml_cfg.get('beets_config'))

    if beets_config_path:
        beets.config.set_file(str(beets_config_path))

        if beets.config['beetstreamnext'].exists():
            bsn_logger.warning(
                f"'{beets_config_path}' has a 'beetstreamnext:' section. "
                "When running BeetstreamNext in standalone mode, this block is ignored. "
                "Move those values to --config's YAML file, BSN_* env vars, or the WebUI instead."
            )

    def _beets_cfg(key: str) -> Optional[str]:
        if not beets_config_path:
            return None
        try:
            return beets.config[key].get(str)
        except confuse.NotFoundError:
            return None

    _cli_flags = {'threads': args.threads, 'host': args.host, 'port': args.port}

    def _yaml_get(key: str) -> Any:
        """
        Get a setting value: CLI flag > env var (if this setting has one) > YAML key
        Coerced to the settings schema's type. None if unset anywhere.
        """
        spec = SETTINGS_SCHEMA[key]
        env_val = get_env(spec['env_var']) if 'env_var' in spec else None
        raw = _cascade_value(_cli_flags.get(key), env_val, yaml_cfg.get(key))
        return coerce_setting(raw, spec['type']) if raw is not None else None

    library_db = _cascade_value(args.library_db, get_env('BEETS_LIBRARY_DB'), yaml_cfg.get('library_db'), _beets_cfg('library'))
    if not library_db:
        parser.error("Beets library.db path is required (--library-db, BEETS_LIBRARY_DB, 'library_db' in --config, or 'library' in --beets-config).")

    library_db = Path(library_db)
    if not library_db.is_file():
        parser.error(f'Beets database not found at `{library_db}`.')

    bsn_db = _cascade_value(args.bsn_db, get_env('BSN_DB_PATH'), yaml_cfg.get('bsn_db'))
    bsn_db_path = Path(bsn_db) if bsn_db else library_db.parent / 'beetstreamnext.db'

    ip_whitelist = _yaml_get('ip_whitelist') or []
    ip_blacklist = _yaml_get('ip_blacklist') or []

    prestartup_config(
        beets_db_path=library_db,
        bsn_db_path=bsn_db_path,
        beets_config_path=beets_config_path,
        ip_whitelist=ip_whitelist,
        ip_blacklist=ip_blacklist,
        standalone=True,
    )

    if args.command == 'clear-cache':
        cmd_clear_cache()
        return

    if args.command in ('create-user', 'update-user', 'delete-user', 'passwd', 'list-users'):

        with app.app_context():
            initialise_db()

            if args.command == 'create-user':
                cmd_create_user()

            elif args.command == 'update-user':
                cmd_update_user(args.username)

            elif args.command == 'delete-user':
                cmd_delete_user(args.username)

            elif args.command == 'passwd':
                cmd_change_passwd(args.username)

            else:
                cmd_list_users()
        return

    yaml_defaults: Dict[str, Any] = {}

    for key in SETTINGS_SCHEMA:
        if key in ('ip_whitelist', 'ip_blacklist'):  # these are resolved differently for pre-startup config
            continue
        value = _yaml_get(key)
        if value is not None:
            yaml_defaults[key] = value

    if ip_whitelist:
        yaml_defaults['ip_whitelist'] = ip_whitelist

    if ip_blacklist:
        yaml_defaults['ip_blacklist'] = ip_blacklist

    # Read db, merge with yaml_defaults, populate the cache, and trigger all LIVE_APPLY_SETTING
    with app.app_context():
        initialise_db()
        settings_store.initialise(yaml_defaults)

    # If 'library_path' or 'music_root' is set from the WebUI, they override the values above
    final_library_db = Path(settings_store.get('library_path')) if settings_store.get('library_path') else library_db
    music_root = _cascade_value(args.music_root, get_env('MUSIC_ROOT'), yaml_cfg.get('music_root'), _beets_cfg('directory'))
    final_music_root = settings_store.get('music_root') or music_root

    if not final_music_root:
        parser.error("Music root directory is required (--music-root, MUSIC_ROOT, 'music_root' in --config, 'directory' in --beets-config, or the 'music_root' setting).")

    if not final_library_db.is_file():
        parser.error(f'Beets database not found at `{final_library_db}`.')

    if final_library_db != library_db:
        app.config['BEETS_DB_PATH'] = final_library_db

    beets.config['timeout'] = DEFAULT_DB_TIMEOUT   # beets.library.Library() has no timeout kwarg?

    lib = Library(str(final_library_db), str(final_music_root))

    host = settings_store.get('host')
    port = settings_store.get('port')
    debug = api_bool(_cascade_value(args.debug, get_env('BSN_DEBUG'), yaml_cfg.get('debug'), default=False))
    force_trust_host = api_bool(_cascade_value(args.force_trust_host, get_env('BSN_FORCE_TRUST_HOST'), yaml_cfg.get('force_trust_host'), default=False))

    playlist_dir = settings_store.get('playlist_dir')
    playlist_dirs = {0: Path(playlist_dir) if playlist_dir else None, 1: None, 2: None}

    run_server(
        lib,
        host=host,
        port=port,
        debug=debug,
        force_trust_host=force_trust_host,
        root_directory=Path(final_music_root),
        playlist_dirs=playlist_dirs,
        yaml_defaults=yaml_defaults,
    )


if __name__ == '__main__':
    main()
