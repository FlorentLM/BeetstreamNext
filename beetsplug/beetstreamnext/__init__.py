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

"""
BeetstreamNext is a Beets.io plugin that exposes OpenSubsonic API endpoints.
"""
import os
import shutil
from pathlib import Path
import logging
import getpass

from beets.plugins import BeetsPlugin
from beets import config
from beets import ui

from flask import Blueprint, render_template_string
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

from beetsplug.beetstreamnext.constants import PROJECT_ROOT, CLEANUP_INTERVAL_SEC, MAX_CACHE_AGE_DAYS, LOOPBACK_IPS
from beetsplug.beetstreamnext.application import app, IP_filter, rate_limiter, LOG_LEVEL, cache_location
from beetsplug.beetstreamnext.db import close_database
from beetsplug.beetstreamnext.console import TermColors, print_box


logging.getLogger('flask').setLevel(LOG_LEVEL)
logging.getLogger('flask.app').setLevel(LOG_LEVEL)


api_bp = Blueprint('api', __name__, url_prefix='/rest')

@app.route('/')
def home():
    lib = app.config.get('lib')
    with lib.transaction() as tx:
        stats = {
            "artists": tx.query("SELECT COUNT(DISTINCT albumartist) FROM albums")[0][0],
            "albums": tx.query("SELECT COUNT(*) FROM albums")[0][0],
            "songs": tx.query("SELECT COUNT(*) FROM items")[0][0],
            "status": "Online"
        }
    template_content = (PROJECT_ROOT / 'index.html').read_text(encoding='utf-8')
    try:
        logo_svg = (app.config['IMAGES_PATH'] / 'beetstreamnext_logo.svg').read_text(encoding='utf-8')
    except OSError:
        app.logger.error("Can't find logo in images directory")
        logo_svg = ''
    return render_template_string(template_content, stats=stats, logo_svg=logo_svg)


import beetsplug.beetstreamnext.albums
import beetsplug.beetstreamnext.artists
import beetsplug.beetstreamnext.coverart
import beetsplug.beetstreamnext.likes
import beetsplug.beetstreamnext.ratings
import beetsplug.beetstreamnext.playlists
import beetsplug.beetstreamnext.playqueue
import beetsplug.beetstreamnext.bookmarks
import beetsplug.beetstreamnext.search
import beetsplug.beetstreamnext.songs
import beetsplug.beetstreamnext.stream
import beetsplug.beetstreamnext.scrobble
import beetsplug.beetstreamnext.lyrics
import beetsplug.beetstreamnext.users
import beetsplug.beetstreamnext.general


# Plugin hook
class BeetstreamNextPlugin(BeetsPlugin):

    def __init__(self):
        super(BeetstreamNextPlugin, self).__init__()
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
        cmd = ui.Subcommand('beetstreamnext', help='run BeetstreamNext server, exposing OpenSubsonic API')

        # Server options
        cmd.parser.add_option('--debug', dest='debug', action='store_true', default=False, help='Run server in debug mode')
        cmd.parser.add_option('--force_trust_host', dest='force_trust_host', action='store_true', default=False, help='Force debug mode on non-localhost')
        cmd.parser.add_option('--port', dest='port', type='int', help='Port to listen on')
        cmd.parser.add_option('--host', dest='host', help='Host to listen on')

        # User management
        cmd.parser.add_option('-c', '--create-user', action='store_true', default=False, help='Create a new user')
        # cmd.parser.add_option('-u', '--update-user', dest='update_user', metavar='USERNAME', help='Update roles for a user')  # TODO
        cmd.parser.add_option('-d', '--delete-user', dest='delete_user', metavar='USERNAME', help='Delete a user')
        cmd.parser.add_option('-p', '--password', dest='passwd_user', metavar='USERNAME', help='Change password for a user')
        cmd.parser.add_option('--list-users', action='store_true',  default=False, help='List all registered users')

        # Maintenance
        cmd.parser.add_option('--clear-cache', action='store_true', help="Clear thumbnail and HTTP cache")

        def func(lib, opts, args):

            app.config['BEETS_DB_PATH'] = Path(config['library'].get())
            app.config['DB_PATH'] = app.config['BEETS_DB_PATH'].parent / 'beetstreamnext.db'

            IP_filter.whitelist = self.config['ip_whitelist'].get(list)
            IP_filter.blacklist = self.config['ip_blacklist'].get(list)

            from beetsplug.beetstreamnext.db import ensure_secret, rotate_session_key

            ensure_secret(app.config['DB_PATH'])
            app.config['SECRET_KEY'] = rotate_session_key(cache_location())

            # Cache clearing
            if opts.clear_cache:
                shutil.rmtree(app.config['THUMBNAIL_CACHE_PATH'], ignore_errors=True)
                try:
                    os.remove(app.config['HTTP_CACHE_PATH'])
                except OSError:
                    pass
                print("Thumbnail cache cleared.")
                print("Admin session key cleared, any active admin sessions have been invalidated.")
                return

            # Create user
            if opts.create_user:
                from beetsplug.beetstreamnext import db
                from beetsplug.beetstreamnext.utils import safe_str

                with app.app_context():
                    db.initialise_db()

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
                        api_key = users.create_user(username, password, admin=is_admin)
                    except ValueError as e:
                        print(f"\n[ERROR] {e}")
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

            # Delete user
            if opts.delete_user:
                with app.app_context():

                    username = opts.delete_user
                    confirm = input(f"Are you sure you want to delete '{username}'? [y/N]: ")
                    if confirm.lower() == 'y':
                        if users.delete_user(username):
                            print(f"User '{username}' deleted.")
                        else:
                            print("User not found.")
                    return

            # List users
            if opts.list_users:
                with app.app_context():
                    all_users = users.load_all_users()
                    header = f"{'Username':<15} | {'Admin':<12} | {'Can stream':<12} | {'Can download':<12}"
                    print(header)
                    print("-" * len(header))
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
                        users.update_user(username, password=new_pw)
                        print("Password updated successfully.")
                    except ValueError as e:
                        print(f"Error: {e}")
                    return

            host = opts.host or self.config['host'].as_str()
            port = opts.port or self.config['port'].get(int)
            debug = opts.debug or self.config['debug'].get(bool)
            force_trust_host = opts.force_trust_host or self.config['force_trust_host'].get(bool)

            app.config['lib'] = lib
            app.config['root_directory'] = Path(config['directory'].get())
            app.config['legacy_auth'] = self.config['legacy_auth'].get(bool)
            app.config['lastfm_api_key'] = self.config['lastfm_api_key'].get(str)
            app.config['never_transcode'] = self.config['never_transcode'].get(bool)
            app.config['fetch_artists_images'] = self.config['fetch_artists_images'].get(bool)
            app.config['save_artists_images'] = self.config['save_artists_images'].get(bool)
            app.config['save_album_art'] = self.config['save_album_art'].get(bool)

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

            if app.config['legacy_auth'] and not self.config['reverse_proxy']:
                if host not in LOOPBACK_IPS:
                    print_box([
                        '',
                        f'{TermColors.WARNING + TermColors.BOLD + TermColors.REVERSE}  WARNING:  {TermColors.ENDC}',
                        '',
                        f'Legacy authentication is enabled, and the server',
                        f"is listening on http://{host}:{port}",
                        f"without a reverse proxy.",
                        '',
                        'Passwords from legacy clients may be',
                        'transmitted in cleartext over HTTP.',
                        '',
                    ], color=TermColors.WARNING)

            possible_paths = [
                (0, self.config['playlist_dir'].as_str()),  # BeetstreamNext's own
                (1, config['playlist']['playlist_dir'].get(None)),  # Playlist plugin
                (2, config['smartplaylist']['playlist_dir'].get(None))  # Smartplaylist plugin
            ]

            playlist_dirs = {}
            used_paths = set()
            for k, path in possible_paths:
                if path and path not in used_paths:
                    playlist_dirs[k] = Path(os.fsdecode(path))
                    used_paths.add(path)
                else:
                    playlist_dirs[k] = None
            app.config['playlist_dirs'] = playlist_dirs

            # Enable CORS if required
            cors_origin = self.config['cors'].get(str)
            supports_creds = self.config['cors_supports_credentials'].get(bool)

            if cors_origin:
                if cors_origin == '*' and supports_creds:
                    print_box([
                        '',
                        f'{TermColors.WARNING + TermColors.BOLD + TermColors.REVERSE}  SECURITY WARNING:  {TermColors.ENDC}',
                        '',
                        f"CORS is set to allow all origins ('*') WITH credentials.",
                        f"This could allow any malicious website you visit to silently interact",
                        f"with your BeetstreamNext server in the background."
                        '',
                        "It is highly recommended to only allow your specific player's URL.",
                        ''
                    ], color=TermColors.WARNING)
                else:
                    app.logger.info(f"Enabling CORS for origin(s): {cors_origin}")

                app.config['CORS_ALLOW_HEADERS'] = "Content-Type"
                origins_list = [o.strip() for o in cors_origin.split(',')] if ',' in cors_origin else cors_origin

                app.config['CORS_RESOURCES'] = {r"/*": {"origins": origins_list}}
                CORS(app, supports_credentials=supports_creds)
            else:
                app.logger.info("CORS is disabled (secure default). Web-based clients will be blocked by browsers.")

            # Allow serving behind a reverse proxy
            if self.config['reverse_proxy']:
                app.wsgi_app = ProxyFix(
                    app.wsgi_app,
                    x_for=1,
                    x_proto=1,
                    x_host=1,
                    x_port=1,
                    x_prefix=1
                )

            with app.app_context():
                from beetsplug.beetstreamnext import db
                from beetsplug.beetstreamnext.playlistprovider import PlaylistProvider

                db.initialise_db()
                app.config['playlist_provider'] = PlaylistProvider()

            if debug:
                app.run(host=host, port=port, debug=True, threaded=True)

            else:
                from waitress import serve
                from paste.translogger import TransLogger

                logging.getLogger('waitress').setLevel(LOG_LEVEL)
                if LOG_LEVEL > logging.INFO:
                    print(f"BeetstreamNext server running on http://{host}:{port}...")
                logged_app = TransLogger(app, setup_console_handler=True)

                serve(logged_app, host=host, port=port, threads=8)

        cmd.func = func
        return [cmd]