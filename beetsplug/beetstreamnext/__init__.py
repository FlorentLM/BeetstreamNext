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
import os
import platform
import shutil
from pathlib import Path
import threading
import getpass
from beets.plugins import BeetsPlugin
from beets import config
from beets import ui
import flask
from flask import g, render_template_string
from flask_cors import CORS

from beetsplug.beetstreamnext.db import close_database

# Flask setup
app = flask.Flask(__name__)
app.teardown_appcontext(close_database)


def cache_location() -> Path:
    if platform.system() == "Windows":
        cache_dir = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif platform.system() == "Darwin":
        cache_dir = Path.home() / "Library" / "Caches"
    else:
        cache_dir = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))

    final_path = cache_dir / "beetstreamnext"
    final_path.mkdir(parents=True, exist_ok=True)
    return final_path


PROJECT_ROOT = Path(os.path.abspath(__file__)).parent

app.config['IMAGES_PATH'] = PROJECT_ROOT / 'images'
app.config['HTTP_CACHE_PATH'] = cache_location() / 'httpcache'
app.config['THUMBNAIL_CACHE_PATH'] = cache_location() / 'thumbnails'
app.config['THUMBNAIL_CACHE_PATH'].mkdir(parents=True, exist_ok=True)


@app.before_request
def before_request():

    if flask.request.path == '/':
        return

    ok, error_code, username = beetsplug.beetstreamnext.users.authenticate(flask.request.values)
    if not ok:
        resp_fmt = flask.request.values.get('f', 'xml')
        return beetsplug.beetstreamnext.utils.subsonic_error(error_code, resp_fmt=resp_fmt)

    g.lib = app.config['lib']
    g.username = username
    g.user_data = beetsplug.beetstreamnext.users.load_user_roles(username)
    g.playlist_provider = app.config['playlist_provider']

    # Pre-build base URL for images so all mapping functions use it in the current request
    r = flask.request.values
    params = {k: r.get(k) for k in ['u', 's', 't', 'p', 'apiKey', 'c', 'v'] if r.get(k)}
    g._art_base_url = flask.url_for('endpoint_get_cover_art', _external=True, **params)


@app.after_request
def add_security_headers(response):
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.route('/')
def home():
    lib = app.config.get('lib')
    stats = {
        "artists": 0,   # TODO
        "albums": len(lib.albums()) if lib else 0,
        "songs": len(lib.items()) if lib else 0,
        "status": "Online"
    }
    template_content = (PROJECT_ROOT / 'index.html' ).open().read()
    try:
        logo_svg = (app.config['IMAGES_PATH'] / 'beetstreamnext_logo.svg').open().read()
    except Exception:
        logo_svg = ''
    # TODO - more colours for the indicator dot: http / https / unencrypted db -> orange / red
    return render_template_string(template_content, stats=stats, logo_svg=logo_svg)


import beetsplug.beetstreamnext.albums
import beetsplug.beetstreamnext.artists
import beetsplug.beetstreamnext.coverart
import beetsplug.beetstreamnext.dummy
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
            'cors': '*',
            'cors_supports_credentials': True,
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

            # Cache clearing
            if opts.clear_cache:
                shutil.rmtree(app.config['THUMBNAIL_CACHE_PATH'], ignore_errors=True)
                try:
                    os.remove(app.config['HTTP_CACHE_PATH'])
                except OSError:
                    pass
                print("Thumbnail cache cleared.")
                return

            # Create user
            if opts.create_user:
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
                        api_key = users.create_user(username, password, admin=is_admin)
                    except ValueError as e:
                        print(f"\n[ERROR] {e}")
                        return

                    print(f"\nUser created successfully!")
                    print(f"API KEY: {api_key}")
                    print("This key is needed by your Subsonic client. Store it safely (it will not be shown again).")

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
            debug = opts.debug

            app.config['lib'] = lib
            app.config['root_directory'] = Path(config['directory'].get())
            app.config['legacy_auth'] = self.config['legacy_auth'].get(bool)
            app.config['lastfm_api_key'] = self.config['lastfm_api_key'].get(str)
            app.config['never_transcode'] = self.config['never_transcode'].get(bool)
            app.config['fetch_artists_images'] = self.config['fetch_artists_images'].get(bool)
            app.config['save_artists_images'] = self.config['save_artists_images'].get(bool)
            app.config['save_album_art'] = self.config['save_album_art'].get(bool)

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

            possible_paths = [
                (0, self.config['playlist_dir'].get(None)),  # BeetstreamNext's own
                (1, config['playlist']['playlist_dir'].get(None)),  # Playlist plugin
                (2, config['smartplaylist']['playlist_dir'].get(None))  # Smartplaylist plugin
            ]

            playlist_dirs = {}
            used_paths = set()
            for k, path in possible_paths:
                if path and path not in used_paths:
                    playlist_dirs[k] = Path(path)
                    used_paths.add(path)
                else:
                    playlist_dirs[k] = None
            app.config['playlist_dirs'] = playlist_dirs

            # Enable CORS if required
            if self.config['cors']:
                self._log.info(f'Enabling CORS with origin: {self.config["cors"]}')

                app.config['CORS_ALLOW_HEADERS'] = "Content-Type"
                app.config['CORS_RESOURCES'] = {r"/*": {"origins": self.config['cors'].get(str)}}
                CORS(app, supports_credentials=self.config['cors_supports_credentials'].get(bool))

            # Allow serving behind a reverse proxy
            if self.config['reverse_proxy']:
                app.wsgi_app = ReverseProxied(app.wsgi_app)

            with app.app_context():
                from beetsplug.beetstreamnext import db
                from beetsplug.beetstreamnext.playlistprovider import PlaylistProvider
                from beetsplug.beetstreamnext.coverart import tidyup_cache

                db.initialise_db()
                app.config['playlist_provider'] = PlaylistProvider()

                threading.Thread(target=tidyup_cache, args=(30,), daemon=True).start()

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
