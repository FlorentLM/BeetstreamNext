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
import getpass
import logging
from pathlib import Path

import beets
from beets.plugins import BeetsPlugin

from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
from waitress import serve
from paste.translogger import TransLogger

from beetsplug.beetstreamnext.utils.text import safe_str
from beetsplug.beetstreamnext.schemas import USER_ROLES_SCHEMA
from beetsplug.beetstreamnext.constants import LOOPBACK_IPS, LOG_LEVEL, bsn_logger, CACHE_LOCATION
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.console import print_box, TermColors
from beetsplug.beetstreamnext.core.security import ip_filter
from beetsplug.beetstreamnext.core.maintenance import clear_caches
from beetsplug.beetstreamnext.core.database import initialise_db, rotate_session_key, ensure_secret
from beetsplug.beetstreamnext.core.users_crud import update_user, delete_user, load_all_users, create_user, load_user_roles
from beetsplug.beetstreamnext.core.playlists import PlaylistProvider
from beetsplug.beetstreamnext.settings import settings_store


class BeetstreamNextPlugin(BeetsPlugin):

    def __init__(self):
        super(BeetstreamNextPlugin, self).__init__('beetstreamnext')

        self.config.add({
            'host': '0.0.0.0',
            'port': 8080,
            'ip_whitelist': [],
            'ip_blacklist': [],
            'cors': '',
            'debug': False,
            'force_trust_host': False,
            'cors_supports_credentials': False,
            'reverse_proxy': False,
            'legacy_auth': True,
            'never_transcode': False,
            'fetch_artists_images': False,
            'save_artists_images': False,
            'save_album_art': False,
            'lastfm_api_key': '',
            'playlist_dir': ''
        })
        self.config['lastfm_api_key'].redact = True

    item_types = {}

    def commands(self):
        cmd = beets.ui.Subcommand('beetstreamnext', help='run BeetstreamNext server, exposing OpenSubsonic API')

        # Server options
        cmd.parser.add_option('--debug', dest='debug', action='store_true', default=False, help='Run server in debug mode')
        cmd.parser.add_option('--force_trust_host', dest='force_trust_host', action='store_true', default=False, help='Force debug mode on non-localhost')
        cmd.parser.add_option('--port', dest='port', type='int', help='Port to listen on')
        cmd.parser.add_option('--host', dest='host', help='Host to listen on')

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

            app.config.update(
                BEETS_DB_PATH=beets_db_path,
                DB_PATH=beets_db_path.parent / 'beetstreamnext.db'
            )

            ip_filter.whitelist = self.config['ip_whitelist'].as_str_seq()
            ip_filter.blacklist = self.config['ip_blacklist'].as_str_seq()

            ensure_secret(app.config['DB_PATH'])
            app.config.update(SECRET_KEY=rotate_session_key(CACHE_LOCATION))

            # Cache clearing
            if opts.clear_cache:
                try:
                    cleared = clear_caches(
                        app.config['THUMBNAIL_CACHE_PATH'],
                        app.config['HTTP_CACHE_PATH']
                    )
                    if cleared:
                        print(f"Cleared: {', '.join(cleared)}.")
                    else:
                        print('Nothing to clear.')
                except RuntimeError as e:
                    print(str(e))
                return

            # Create user
            if opts.create_user:
                with app.app_context():
                    initialise_db()

                    unsername_ok = False
                    while not unsername_ok:
                        username = input('Username: ')
                        username_cleaned = safe_str(username)
                        if username_cleaned != username:
                            invalid_chars = {c for c in username if c not in username_cleaned}
                            message = 'invalid characters' if len(invalid_chars) > 1 else 'an invalid character'
                            chars_print = "'" + "".join(invalid_chars) + "'"
                            unsername_ok = input(f"Username starts or ends with {message}: {chars_print}\n"
                                                 f"Use '{username_cleaned}' instead? [y/n]: ").lower() == 'y'
                        else:
                            unsername_ok = True
                    password = getpass.getpass('Password: ')
                    is_admin = input('Admin? [y/n]: ').lower() == 'y'

                    try:
                        api_key = create_user(username, password, admin=is_admin)
                    except ValueError as e:
                        print(f'\n[ERROR] {e}')
                        return

                    print_box([
                        '',
                        f"{TermColors.OKGREEN + TermColors.BOLD}User '{username_cleaned}' created successfully.{TermColors.ENDC}",
                        '',
                        f'USER API KEY: {api_key}',
                        '',
                        '  ▶  Enter this key in your Subsonic client instead of a password.',
                        "  ▶  It won't be shown again. Store it safely.",
                        '',
                    ])

                return

            # Update user roles
            if opts.update_user:
                with app.app_context():
                    username = opts.update_user
                    current_data = load_user_roles(username)
                    if not current_data:
                        print(f"User '{username}' not found.")
                        return

                    print(f'Updating roles for user: {username}')
                    print('(Press Enter to keep current value)')
                    updates = {}
                    for role_name, label, _ in USER_ROLES_SCHEMA:
                        curr_status = 'Enabled' if current_data.get(role_name) else 'Disabled'
                        val = input(f'{label} (currently {curr_status}) [y/n]: ').lower()
                        if val == 'y':
                            updates[role_name] = True
                        elif val == 'n':
                            updates[role_name] = False

                    if updates:
                        try:
                            update_user(username, **updates)
                            print(f"Successfully updated roles for '{username}'.")
                        except ValueError as e:
                            print(f'Error: {e}')
                    else:
                        print("No roles changed.")
                return

            # Delete user
            if opts.delete_user:
                with app.app_context():

                    username = opts.delete_user
                    confirm = input(f"Are you sure you want to delete '{username}'? [y/N]: ")
                    if confirm.lower() == 'y':
                        if delete_user(username):
                            print(f"User '{username}' deleted.")
                        else:
                            print('User not found.')
                    return

            # List users
            if opts.list_users:
                with app.app_context():
                    all_users = load_all_users()
                    header = f"{'Username':<15} | {'Admin':<12} | {'Can stream':<12} | {'Can download':<12}"
                    print(header)
                    print('-' * len(header))
                    for u in all_users:
                        print(
                            f"{u['username']:<15} |"
                            f" {bool(u['adminRole']):<12} |"
                            f" {bool(u['streamRole']):<12} |"
                            f" {bool(u['downloadRole']):<12}"
                        )
                    return

            # Update password
            if opts.passwd_user:
                with app.app_context():
                    username = opts.passwd_user
                    new_pw = getpass.getpass(f"New password for '{username}': ")
                    try:
                        update_user(username, password=new_pw)
                        print('Password updated successfully.')
                    except ValueError as e:
                        print(f'Error: {e}')
                    return

            host = opts.host or self.config['host'].as_str()
            port = opts.port or self.config['port'].get(int)
            debug = opts.debug or self.config['debug'].get(bool)
            force_trust_host = opts.force_trust_host or self.config['force_trust_host'].get(bool)

            yaml_defaults = {
                'cors_origins': self.config['cors'].get(str),
                'cors_supports_credentials': self.config['cors_supports_credentials'].get(bool),
                'reverse_proxy': self.config['reverse_proxy'].get(bool),
                'legacy_auth': self.config['legacy_auth'].get(bool),
                'never_transcode': self.config['never_transcode'].get(bool),
                'fetch_artists_images': self.config['fetch_artists_images'].get(bool),
                'save_artists_images': self.config['save_artists_images'].get(bool),
                'save_album_art': self.config['save_album_art'].get(bool),
                'lastfm_api_key': self.config['lastfm_api_key'].get(str),
                'ip_whitelist': self.config['ip_whitelist'].as_str_seq(),
                'ip_blacklist': self.config['ip_blacklist'].as_str_seq(),
            }

            with app.app_context():
                initialise_db()
                app.config.update(playlist_provider=PlaylistProvider())

                # Read db, merge with yaml_defaults, populate the cache, and trigger all LIVE_APPLY_SETTING
                settings_store.initialise(yaml_defaults)

            if debug and host not in LOOPBACK_IPS:
                if force_trust_host:
                    print_box([
                        '',
                        f'{TermColors.WARNING + TermColors.BOLD + TermColors.REVERSE}  !!! SUPER IMPORTANT WARNING !!!  {TermColors.ENDC}',
                        '',
                        f'Debug mode is force-enabled on {host}.',
                        f'The Werkzeug debugger allows arbitrary remote code execution.',
                        '',
                        "I hope you know what you're doing!",
                        '',
                    ], color=TermColors.WARNING)

                else:
                    print_box([
                        '',
                        f'{TermColors.FAIL + TermColors.BOLD + TermColors.REVERSE}  STARTUP ABORTED:  {TermColors.ENDC}',
                        '',
                        f'Debug mode can only be used on localhost.',
                        f'The Werkzeug debugger allows arbitrary remote code execution.',
                        '',
                    ], color=TermColors.FAIL)
                    return

            if settings_store.get('legacy_auth') and not settings_store.get('reverse_proxy'):
                if host not in LOOPBACK_IPS:
                    print_box([
                        '',
                        f'{TermColors.WARNING + TermColors.BOLD + TermColors.REVERSE}  WARNING:  {TermColors.ENDC}',
                        '',
                        f'Legacy authentication is enabled, and the server',
                        f'is listening on http://{host}:{port}',
                        f'without a reverse proxy.',
                        '',
                        'Passwords from legacy clients may be',
                        'transmitted in cleartext over HTTP.',
                        '',
                    ], color=TermColors.WARNING)

            possible_paths = [
                (0, self.config['playlist_dir'].as_str()),  # BeetstreamNext's own
                (1, beets.config['playlist']['playlist_dir'].get(None)),  # Playlist plugin
                (2, beets.config['smartplaylist']['playlist_dir'].get(None))  # Smartplaylist plugin
            ]

            playlist_dirs = {}
            used_paths = set()
            for k, path in possible_paths:
                if path and path not in used_paths:
                    playlist_dirs[k] = Path(os.fsdecode(path))
                    used_paths.add(path)
                else:
                    playlist_dirs[k] = None

            # App-level things that don't belong in db settings
            app.config.update(
                lib=lib,
                root_directory=Path(beets.config['directory'].get()),
                playlist_dirs=playlist_dirs
            )

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
                        f"CORS is set to allow all origins ('*') WITH credentials.",
                        f'This could allow any malicious website you visit to silently interact',
                        f'with your BeetstreamNext server in the background.',
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

            # Allow serving behind a reverse proxy
            if settings_store.get('reverse_proxy'):
                app.wsgi_app = ProxyFix(
                    app.wsgi_app,
                    x_for=1,
                    x_proto=1,
                    x_host=1,
                    x_port=1,
                    x_prefix=1
                )
                app.config.update(SESSION_COOKIE_SECURE=True)


            if debug:
                app.run(host=host, port=port, debug=True, threaded=True)

            else:
                logging.getLogger('waitress').setLevel(LOG_LEVEL)
                if LOG_LEVEL > logging.INFO:
                    print(f'BeetstreamNext server running on http://{host}:{port}...')
                logged_app = TransLogger(app, setup_console_handler=True)

                serve(logged_app, host=host, port=port, threads=8)

        cmd.func = func

        return [cmd]