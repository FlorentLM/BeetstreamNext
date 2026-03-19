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

"""BeetstreamNext is a Beets.io plugin that exposes SubSonic API endpoints."""
import getpass
from pathlib import Path

from beets.plugins import BeetsPlugin
from beets import config
from beets import ui
import flask
from flask import g
from flask_cors import CORS

# Flask setup
app = flask.Flask(__name__)

@app.before_request
def before_request():
    g.lib = app.config['lib']

    if flask.request.path == '/':
        return

    from beetsplug.beetstreamnext.authentication import authenticate
    from beetsplug.beetstreamnext.db import load_user_roles, load_user_likes
    from beetsplug.beetstreamnext.utils import subsonic_error

    ok, error_code, username = authenticate(flask.request.values)
    if not ok:
        resp_fmt = flask.request.values.get('f', 'xml')
        return subsonic_error(error_code, resp_fmt=resp_fmt)

    g.username = username
    g.user_data = load_user_roles(username)
    g.liked = load_user_likes(username)

    if app.config.get('playlist_provider') is None:
        from beetsplug.beetstreamnext.playlistprovider import PlaylistProvider
        app.config['playlist_provider'] = PlaylistProvider()
    g.playlist_provider = app.config['playlist_provider']

@app.route('/')
def home():
    return "BeetstreamNext server running"

import beetsplug.beetstreamnext.albums
import beetsplug.beetstreamnext.artists
import beetsplug.beetstreamnext.coverart
import beetsplug.beetstreamnext.dummy
import beetsplug.beetstreamnext.likes
import beetsplug.beetstreamnext.playlists
import beetsplug.beetstreamnext.search
import beetsplug.beetstreamnext.songs
import beetsplug.beetstreamnext.users
import beetsplug.beetstreamnext.general
import beetsplug.beetstreamnext.authentication


# Plugin hook
class BeetstreamNextPlugin(BeetsPlugin):

    def __init__(self):
        super(BeetstreamNextPlugin, self).__init__()
        self.config.add({
            'host': '0.0.0.0',
            'port': 8080,
            'cors': '*',
            'cors_supports_credentials': True,
            'reverse_proxy': False,
            'include_paths': False,
            'never_transcode': False,
            'fetch_artists_images': False,
            'save_artists_images': True,
            'lastfm_api_key': '',
            'playlist_dir': '',
            'legacy_auth': True
        })
        self.config['lastfm_api_key'].redact = True

    item_types = {}

    # album_types = {
    #     'last_liked_album': DateType(),
    #     'stars_rating_album': types.INTEGER
    # }

    def commands(self):
        cmd = ui.Subcommand('beetstreamnext', help='run BeetstreamNext server, exposing OpenSubsonic API')
        cmd.parser.add_option('-d', '--debug', action='store_true', default=False, help='Debug mode')
        cmd.parser.add_option('-u', '--user', action='store_true', default=False, help='Create a new user')

        def func(lib, opts, args):
            # Shared DB path setup
            db_path = Path(config['library'].get()).parent / 'beetstreamnext.db'
            app.config['DB_PATH'] = db_path

            if opts.user:
                from beetsplug.beetstreamnext import db
                with app.app_context():
                    db.initialise_db()

                    legacy_enabled = self.config['legacy_auth'].get(bool)

                    if legacy_enabled and db.get_cipher() is None:
                        print("\n[WARNING] Legacy authentication is enabled, but BEETSTREAMNEXT_KEY env var is not set.")
                        print("Without it, passwords for legacy Subsonic clients will be stored in PLAINTEXT.")
                        confirm = input("Continue anyway? [y/N]: ")
                        if confirm.lower() != 'y':
                            return

                    username = input('Username: ')
                    password = getpass.getpass('Password: ')
                    is_admin = input('Admin? [y/n]: ').lower() == 'y'

                    try:
                        api_key = db.create_user(username, password, admin=is_admin)
                    except ValueError as e:
                        print(f"\n[ERROR] {e}")
                        return
                    print(f"\nUser created successfully!")
                    print(f"API KEY: {api_key}")
                    print("This key is needed by your Subsonic client. Store it safely (it will not be shown again).")
                return

            args = ui.decargs(args)
            if args:
                self.config['host'] = args.pop(0)
            if args:
                self.config['port'] = int(args.pop(0))

            app.config['lib'] = lib
            app.config['root_directory'] = Path(config['directory'].get())
            app.config['legacy_auth'] = self.config['legacy_auth'].get(bool)
            app.config['INCLUDE_PATHS'] = self.config['include_paths']
            app.config['lastfm_api_key'] = self.config['lastfm_api_key'].get(None)

            app.config['never_transcode'] = self.config['never_transcode'].get(False)
            app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False

            app.config['fetch_artists_images'] = self.config['fetch_artists_images'].get(False)
            app.config['save_artists_images'] = self.config['save_artists_images'].get(False)

            app.config['DB_PATH'] = Path(config['library'].get()).parent / 'beetstreamnext.db'

            # Total number of items in Beets database (only used to detect deletions in getIndexes endpoint)
            app.config['nb_items'] = float('inf') # set the first time a client queries the getIndexes endpoint

            possible_paths = [
                (0, self.config['playlist_dir'].get(None)),  # BeetstreamNext's own
                (1, config['playlist']['playlist_dir'].get(None)),  # Playlist plugin
                (2, config['smartplaylist']['playlist_dir'].get(None))  # Smartplaylist plugin
            ]

            playlist_dirs = {}
            used_paths = set()
            for k, path in possible_paths:
                if path not in used_paths:
                    playlist_dirs[k] = path
                    used_paths.add(path)
                else:
                    playlist_dirs[k] = None
            app.config['playlist_dirs'] = playlist_dirs

            # Enable CORS if required
            if self.config['cors']:
                self._log.info(f'Enabling CORS with origin: {self.config["cors"]}')
                app.config['CORS_ALLOW_HEADERS'] = "Content-Type"
                app.config['CORS_RESOURCES'] = {
                    r"/*": {"origins": self.config['cors'].get(str)}
                }
                CORS(
                    app,
                    supports_credentials=self.config[
                        'cors_supports_credentials'
                    ].get(bool)
                )

            # Allow serving behind a reverse proxy
            if self.config['reverse_proxy']:
                app.wsgi_app = ReverseProxied(app.wsgi_app)

            with app.app_context():
                from beetsplug.beetstreamnext import db
                db.initialise_db()

            host = self.config['host'].as_str()
            port = self.config['port'].get(int)
            debug = opts.debug

            if debug and host not in ['127.0.0.1', 'localhost']:
                print(f"[ERROR] Debug mode cannot be used with host {host}. "
                      "The Werkzeug debugger allows arbitrary remote code execution. "
                      "Use 127.0.0.1 for local debugging.")
                return

            if app.config['legacy_auth'] and not self.config['reverse_proxy']:
                if host not in ['127.0.0.1', 'localhost']:
                    print(
                        "[WARNING] Legacy authentication is enabled and the server is listening on "
                        f"{host}:{port} without a reverse proxy. Passwords from legacy "
                        "clients may be transmitted in cleartext over HTTP. "
                        "Use a reverse proxy with TLS or disable legacy_auth."
                    )

            app.run(
                host=host,
                port=port,
                debug=debug,
                threaded=True
            )

        cmd.func = func
        return [cmd]


class ReverseProxied:
    """
    Wrap the application in this middleware and configure the
    front-end server to add these headers, to let you quietly bind
    this to a URL other than / and to an HTTP scheme that is
    different than what is used locally.

    In nginx:
    location /myprefix {
        proxy_pass http://192.168.0.1:5001;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Scheme $scheme;
        proxy_set_header X-Script-Name /myprefix;
        }

    From: http://flask.pocoo.org/snippets/35/

    :param app: the WSGI application
    """
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        script_name = environ.get('HTTP_X_SCRIPT_NAME', '')
        if script_name:
            environ['SCRIPT_NAME'] = script_name
            path_info = environ['PATH_INFO']
            if path_info.startswith(script_name):
                environ['PATH_INFO'] = path_info[len(script_name):]

        scheme = environ.get('HTTP_X_SCHEME', '')
        if scheme:
            environ['wsgi.url_scheme'] = scheme
        return self.app(environ, start_response)
