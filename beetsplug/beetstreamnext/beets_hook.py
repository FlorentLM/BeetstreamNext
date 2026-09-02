# -*- coding: utf-8 -*-
# This file is part of beets.
# Copyright 2016, Adrian Sampson.
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
import os
import sys
import optparse
from pathlib import Path
from typing import Any, List, Optional

import beets
from beets.plugins import BeetsPlugin

from beetsplug.beetstreamnext.schemas import SETTINGS_SCHEMA
from beetsplug.beetstreamnext.settings import coerce_setting, settings_store
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.startup import run_server, prestartup_config
from beetsplug.beetstreamnext.core.database import initialise_db
from beetsplug.beetstreamnext.core.commands import (
    cmd_clear_cache, cmd_create_user, cmd_update_user, cmd_delete_user, cmd_list_users, cmd_change_passwd
)


def _detect_config_override(argv: List[str]) -> Optional[str]:
    """
    Look for an eventual explicit -c/--config arg that beets could have been launched with.
    """
    parser = optparse.OptionParser(add_help_option=False)
    parser.disable_interspersed_args()
    parser.add_option('--format-item')
    parser.add_option('--format-album')
    parser.add_option('-l', '--library')
    parser.add_option('-d', '--directory')
    parser.add_option('-v', '--verbose', action='count')
    parser.add_option('-c', '--config')
    parser.add_option('-p', '--plugins')
    parser.add_option('-P', '--disable-plugins')

    try:
        options, _ = parser.parse_args(list(argv))
    except optparse.OptParseError:
        return None

    return options.config


class BeetstreamNextPlugin(BeetsPlugin):

    def __init__(self):
        super(BeetstreamNextPlugin, self).__init__('beetstreamnext')

        self.config.add({
            'host': SETTINGS_SCHEMA['host']['default'],
            'port': SETTINGS_SCHEMA['port']['default'],
            'ip_whitelist': SETTINGS_SCHEMA['ip_whitelist']['default'],
            'ip_blacklist': SETTINGS_SCHEMA['ip_blacklist']['default'],
            'cors_origins': SETTINGS_SCHEMA['cors_origins']['default'],
            'debug': False,
            'force_trust_host': False,
            'cors_supports_credentials': SETTINGS_SCHEMA['cors_supports_credentials']['default'],
            'reverse_proxy': SETTINGS_SCHEMA['reverse_proxy']['default'],
            'proxy_hops': SETTINGS_SCHEMA['proxy_hops']['default'],
            'legacy_auth': SETTINGS_SCHEMA['legacy_auth']['default'],
            'never_transcode': SETTINGS_SCHEMA['never_transcode']['default'],
            'fetch_artists_images': SETTINGS_SCHEMA['fetch_artists_images']['default'],
            'save_artists_images': SETTINGS_SCHEMA['save_artists_images']['default'],
            'save_album_art': SETTINGS_SCHEMA['save_album_art']['default'],
            'lastfm_api_key': SETTINGS_SCHEMA['lastfm_api_key']['default'],
            'playlist_dir': SETTINGS_SCHEMA['playlist_dir']['default'],
            'threads': SETTINGS_SCHEMA['threads']['default'],
        })
        self.config['lastfm_api_key'].redact = True

    item_types = {}

    def commands(self):
        cmd = beets.ui.Subcommand('beetstreamnext', help='run BeetstreamNext server, exposing OpenSubsonic API')

        # Server options
        cmd.parser.add_option('--debug', dest='debug', action='store_true', default=False, help='Run server in debug mode')
        cmd.parser.add_option('--force-trust-host', dest='force_trust_host', action='store_true', default=False, help='Force debug mode on non-localhost')
        cmd.parser.add_option('--port', dest='port', type='int', help='Port to listen on')
        cmd.parser.add_option('--host', dest='host', help='Host(s) to listen on, comma-separated (e.g. 192.168.1.10,100.64.0.5)')
        cmd.parser.add_option('--threads', dest='threads', type='int', help='Waitress worker threads')
        cmd.parser.add_option('--playlist-dir', dest='playlist_dir', help="Directory for BeetstreamNext's own playlists")

        # User management
        cmd.parser.add_option('-c', '--create-user', action='store_true', default=False, help='Create a new user')
        cmd.parser.add_option('-u', '--update-user', dest='update_user', metavar='USERNAME', help='Update roles for a user')
        cmd.parser.add_option('-d', '--delete-user', dest='delete_user', metavar='USERNAME', help='Delete a user')
        cmd.parser.add_option('-p', '--password', dest='passwd_user', metavar='USERNAME', help='Change password for a user')
        cmd.parser.add_option('--list-users', action='store_true',  default=False, help='List all registered users')

        # Maintenance
        cmd.parser.add_option('--clear-cache', action='store_true', help="Clear thumbnail and HTTP cache")

        def func(lib, opts, args):

            beets_db_path = Path(beets.config['library'].get())

            if not beets_db_path.is_file():
                raise RuntimeError(f'Beets database not found at `{beets_db_path}`.')

            prestartup_config(
                beets_db_path=beets_db_path,
                bsn_db_path=beets_db_path.parent / 'beetstreamnext.db',
                beets_config_path=_detect_config_override(sys.argv[1:]),
                ip_whitelist=self.config['ip_whitelist'].as_str_seq(),
                ip_blacklist=self.config['ip_blacklist'].as_str_seq(),
            )

            # Cache clearing
            if opts.clear_cache:
                cmd_clear_cache()
                return

            # Create a new user
            if opts.create_user:
                with app.app_context():
                    initialise_db()
                    cmd_create_user()
                return

            # Update a user's roles
            if opts.update_user:
                with app.app_context():
                    cmd_update_user(opts.update_user)
                return

            # Delete a user
            if opts.delete_user:
                with app.app_context():
                    cmd_delete_user(opts.delete_user)
                return

            # List all users
            if opts.list_users:
                with app.app_context():
                    cmd_list_users()
                return

            # Change a user's password
            if opts.passwd_user:
                with app.app_context():
                    cmd_change_passwd(opts.passwd_user)
                return

            def _beets_yaml_get(key: str, cli_value: Any = None) -> Any:
                """
                Get a setting value: CLI flag > beets' config key
                Coerced to the settings schema's type.
                """
                stypes_map = {'bool': bool, 'int': int, 'str': str}

                spec = SETTINGS_SCHEMA[key]
                if cli_value:
                    raw = cli_value
                elif spec['type'] == 'list[str]':
                    raw = self.config[key].as_str_seq()
                else:
                    raw = self.config[key].get(stypes_map[spec['type']])
                return coerce_setting(raw, spec['type'])

            debug = opts.debug or self.config['debug'].get(bool)
            force_trust_host = opts.force_trust_host or self.config['force_trust_host'].get(bool)

            yaml_defaults = {
                'host': _beets_yaml_get('host', cli_value=opts.host),
                'port': _beets_yaml_get('port', cli_value=opts.port),
                'threads': _beets_yaml_get('threads', cli_value=opts.threads),
                'playlist_dir': _beets_yaml_get('playlist_dir', cli_value=opts.playlist_dir),
            }

            for key, spec in SETTINGS_SCHEMA.items():
                if key in yaml_defaults or 'env_var' not in spec:
                    continue
                yaml_defaults[key] = _beets_yaml_get(key)

            with app.app_context():
                initialise_db()
                settings_store.initialise(yaml_defaults)

            host = settings_store.get('host')
            port = settings_store.get('port')

            possible_paths = [
                (0, settings_store.get('playlist_dir')),  # BeetstreamNext's own
                (1, beets.config['playlist']['playlist_dir'].get(None)),  # Playlist plugin
                (2, beets.config['smartplaylist']['playlist_dir'].get(None))  # Smartplaylist plugin
            ]

            playlist_dirs = {k: Path(os.fsdecode(path)) if path else None for k, path in possible_paths}

            run_server(
                lib,
                host=host,
                port=port,
                debug=debug,
                force_trust_host=force_trust_host,
                root_directory=Path(beets.config['directory'].get()),
                playlist_dirs=playlist_dirs,
                yaml_defaults=yaml_defaults,
            )

        cmd.func = func

        return [cmd]