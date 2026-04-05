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
import time
import platform
import shutil
from datetime import datetime
from pathlib import Path
import threading
import getpass
from typing import Dict, List
import logging

from beets.plugins import BeetsPlugin
from beets import config
from beets import ui
import flask
from flask import g, render_template_string
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix

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


_LOOPBACK_IPS = frozenset({'127.0.0.1', 'localhost', '::1'})

PROJECT_ROOT = Path(os.path.abspath(__file__)).parent

app.config['IMAGES_PATH'] = PROJECT_ROOT / 'images'
app.config['HTTP_CACHE_PATH'] = cache_location() / 'httpcache.sqlite'
app.config['THUMBNAIL_CACHE_PATH'] = cache_location() / 'thumbnails'
app.config['THUMBNAIL_CACHE_PATH'].mkdir(parents=True, exist_ok=True)


# Cache cleanup
_cleanup_lock = threading.Lock()
_last_cleanup: float = 0.0
_CLEANUP_INTERVAL = 24 * 3600  # once per day
_MAX_CACHE_AGE_DAYS = 30


def _run_periodic_things():
    """
    Runs housekeeping periodically.
    Deletes old cached thumbnails, purges rate limiting store.
    """

    global _last_cleanup

    now = time.time()
    if now - _last_cleanup < _CLEANUP_INTERVAL:
        return

    if not _cleanup_lock.acquire(blocking=False):
        return  # another thread already doing it

    try:
        # check inside the lock if another thread may have just finished
        if now - _last_cleanup < _CLEANUP_INTERVAL:
            return
        _last_cleanup = now
    finally:
        _cleanup_lock.release()

    def _background_maintenance():
        app.logger.info(f"[{datetime.fromtimestamp(now)}] Starting background maintenance...")

        # Sweep stale IPs from rate-limit dict
        with _auth_lock:    # only hold lock to grab the keys
            all_ips = list(_FAILED_AUTH_ATTEMPTS.keys())

        stale = []
        for ip in all_ips:  # reads are ok without lock
            attempts = _FAILED_AUTH_ATTEMPTS.get(ip, [])
            if not attempts or (now - max(attempts) > _BLOCK_TIME_SECONDS):
                stale.append(ip)

        if stale:
            with _auth_lock:   # only lock when actually deleting
                for ip in stale:
                    _FAILED_AUTH_ATTEMPTS.pop(ip, None)

        # Tidy cache
        cache_dir = app.config['THUMBNAIL_CACHE_PATH']
        if cache_dir.exists():
            max_age_seconds = _MAX_CACHE_AGE_DAYS * 86400
            try:
                for f in cache_dir.iterdir():
                    if f.suffix == '.jpg' and (now - f.stat().st_mtime > max_age_seconds):
                        f.unlink(missing_ok=True)
            except Exception as e:
                app.logger.error(f"Error cleaning thumbnail cache: {e}")

        app.logger.info(f"[{datetime.fromtimestamp(now)}] Background maintenance complete.")

    thread = threading.Thread(target=_background_maintenance, daemon=True)
    thread.start()


# Rate-limit auth failures
_auth_lock = threading.Lock()
_FAILED_AUTH_ATTEMPTS: Dict[str, List[float]] = {}   # IP -> list of failed attempt timestamps
_MAX_AUTH_FAILURES = 5      # Block for 5 minutes
_BLOCK_TIME_SECONDS = 300   # after 5 failed attempts


@app.before_request
def _before_request():
    from beetsplug.beetstreamnext.utils import safe_str

    r = flask.request.values
    resp_fmt = r.get('f', default='xml', type=safe_str)

    if flask.request.path == '/':
        return

    if flask.request.path.rstrip('/') in ('/rest/getOpenSubsonicExtensions', '/rest/getOpenSubsonicExtensions.view'):
        return

    client_ip = str(flask.request.remote_addr)
    now = time.time()

    from beetsplug.beetstreamnext.utils import subsonic_error
    from beetsplug.beetstreamnext.users import authenticate, load_user_roles

    if client_ip not in _LOOPBACK_IPS:

        # IP whitelist / blacklist
        whitelist = app.config.get('ip_whitelist', [])

        if whitelist and client_ip not in whitelist:
            app.logger.info(f"[{datetime.fromtimestamp(now)}] IP {client_ip} not in whitelist: access denied.")
            return subsonic_error(50, message="Access denied: IP not in whitelist.", resp_fmt=resp_fmt)

        blacklist = app.config.get('ip_blacklist', [])
        if blacklist and client_ip in blacklist:
            app.logger.info(f"[{datetime.fromtimestamp(now)}] IP {client_ip} is blacklisted: access denied.")
            return subsonic_error(50, message="Access denied: IP is blacklisted.", resp_fmt=resp_fmt)

        # Rate limiting
        with _auth_lock:
            recent = [t for t in _FAILED_AUTH_ATTEMPTS.get(client_ip, [])
                      if now - t < _BLOCK_TIME_SECONDS]
            if recent:
                _FAILED_AUTH_ATTEMPTS[client_ip] = recent
            else:
                _FAILED_AUTH_ATTEMPTS.pop(client_ip, None)
            blocked = len(recent) >= _MAX_AUTH_FAILURES

        if blocked:
            return subsonic_error(40, message="Too many failed login attempts. Try again later.", resp_fmt=resp_fmt)

    # Attempt authentication
    ok, error_code, username = authenticate(r)
    if not ok:
        with _auth_lock:
            _FAILED_AUTH_ATTEMPTS.setdefault(client_ip, []).append(now)   # failed attempt : record it
        return subsonic_error(error_code, resp_fmt=resp_fmt)

    with _auth_lock:
        _FAILED_AUTH_ATTEMPTS.pop(client_ip, None)   # auth success: clear failure history

    from beetsplug.beetstreamnext.utils import grab_auth_params

    g.lib = app.config['lib']
    g.username = username
    g.user_data = load_user_roles(username)
    g.playlist_provider = app.config['playlist_provider']
    g._art_base_url = flask.url_for('endpoint_get_cover_art', _external=True, **grab_auth_params())

    _run_periodic_things()


@app.after_request
def _add_security_headers(response):
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


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
    except Exception:
        logo_svg = ''
    # TODO - more colours for the indicator dot: http / https / unencrypted db -> orange / red
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
            app.config['ip_whitelist'] = self.config['ip_whitelist'].get(list)
            app.config['ip_blacklist'] = self.config['ip_blacklist'].get(list)

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
                from beetsplug.beetstreamnext.utils import safe_str

                with app.app_context():
                    db.initialise_db()

                    legacy_enabled = self.config['legacy_auth'].get(bool)

                    if legacy_enabled and db.get_cipher() is None:
                        print("\n[WARNING] Legacy authentication is enabled, but BEETSTREAMNEXT_KEY env var is not set.")
                        print("Without it, passwords for legacy Subsonic clients will be stored in PLAINTEXT.")
                        confirm = input("Continue anyway? [y/N]: ")
                        if confirm.lower() != 'y':
                            return

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

            if debug and host not in _LOOPBACK_IPS:
                if force_trust_host:
                    print(f"[!!! SUPER IMPORTANT WARNING !!!] Debug mode is force-enabled on {host}. "
                          "The Werkzeug debugger allows arbitrary remote code execution. "
                          "I hope you know what you're doing!")
                else:
                    print(f"[ERROR] Debug mode can only be used on localhost "
                          f"(the debugger allows arbitrary remote code execution).")
                    return

            if app.config['legacy_auth'] and not self.config['reverse_proxy']:
                if host not in _LOOPBACK_IPS:
                    print(
                        "[WARNING] Legacy authentication is enabled and the server is listening on "
                        f"{host}:{port} without a reverse proxy. Passwords from legacy "
                        "clients may be transmitted in cleartext over HTTP. "
                    )

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
                    print(
                        "\n[SECURITY WARNING] CORS is set to allow all origins ('*') WITH credentials. "
                        "This tells the server to allow any website to interact with your API. "
                        "If you use a web-based Subsonic player, it is highly recommended to set 'cors' "
                        "specifically to that player's URL.\n"
                    )
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

                logging.getLogger('waitress').setLevel(logging.INFO)

                print(f"BeetstreamNext server running on {host}:{port}...")
                logged_app = TransLogger(app, setup_console_handler=True)
                serve(logged_app, host=host, port=port, threads=8)

        cmd.func = func
        return [cmd]