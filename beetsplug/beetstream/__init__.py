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

"""Beetstream is a Beets.io plugin that exposes SubSonic API endpoints."""

from beets.plugins import BeetsPlugin
from beets.dbcore import types
from beets.library import DateType
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

@app.route('/')
def home():
    return "Beetstream server running"

from beetsplug.beetstream.utils import *
import beetsplug.beetstream.albums
import beetsplug.beetstream.artists
import beetsplug.beetstream.coverart
import beetsplug.beetstream.dummy
import beetsplug.beetstream.playlists
import beetsplug.beetstream.search
import beetsplug.beetstream.songs
import beetsplug.beetstream.users
import beetsplug.beetstream.general
import beetsplug.beetstream.authentication


# Plugin hook
class BeetstreamPlugin(BeetsPlugin):
    def __init__(self):
        super(BeetstreamPlugin, self).__init__()
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
            'users_storage': Path(config['library'].get()).parent / 'beetstream_users.bin',
        })
        self.config['lastfm_api_key'].redact = True

    item_types = {
        # We use the same fields as the MPDStats plugin for interoperability
        'play_count': types.INTEGER,
        'last_played': DateType(),
        'last_liked': DateType(),
    }

    # album_types = {
    #     'last_liked_album': DateType(),
    #     'stars_rating_album': types.INTEGER
    # }

    def commands(self):
        cmd = ui.Subcommand('beetstream', help='run Beetstream server, exposing OpenSubsonic API')
        cmd.parser.add_option('-d', '--debug', action='store_true', default=False, help='Debug mode')
        cmd.parser.add_option('-k', '--key', action='store_true', default=False, help='Generate a key to store passwords')

        def func(lib, opts, args):
            if opts.key:
                users_storage = Path(self.config['users_storage'].get())

                if not users_storage.is_file():
                    key = authentication.generate_key()
                    print(f'Here is your new key (store it safely): {key}')
                    yn_input = input('No existing users, create one? [y/n]: ')
                    if 'y' in yn_input.lower():
                        username = input('Username: ')
                        password = input('Password: ')
                        success = authentication.update_user(users_storage, key, {username: password})
                        if success:
                            print('User created.')
                else:
                    yn_input = input('Users storage file exists, update key? [y/n]: ')
                    if 'y' in yn_input.lower():
                        current_key = input('Current key: ').encode()
                        new_key = authentication.generate_key()
                        success = authentication.update_key(users_storage, current_key, new_key)
                        if success:
                            print(f'Key updated (store it safely): {new_key.decode()}')
                return

            args = ui.decargs(args)
            if args:
                self.config['host'] = args.pop(0)
            if args:
                self.config['port'] = int(args.pop(0))

            app.config['lastfm_api_key'] = self.config['lastfm_api_key'].get(None)

            app.config['fetch_artists_images'] = self.config['fetch_artists_images'].get(False)
            app.config['save_artists_images'] = self.config['save_artists_images'].get(False)

            app.config['root_directory'] = Path(config['directory'].get())
            app.config['users_storage'] = Path(self.config['users_storage'].get())

            # Total number of items in the Beets database (only used to detect deletions in getIndexes endpoint)
            # We initialise to +inf at Beetstream start, so the real count is set the first time a client queries
            # the getIndexes endpoint
            app.config['nb_items'] = float('inf')

            app.config['lib'] = lib
            app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False
            app.config['INCLUDE_PATHS'] = self.config['include_paths']
            app.config['never_transcode'] = self.config['never_transcode'].get(False)

            possible_paths = [
                (0, self.config['playlist_dir'].get(None)),  # Beetstream's own
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

            # Start the web application
            app.run(host=self.config['host'].as_str(),
                    port=self.config['port'].get(int),
                    debug=opts.debug, threaded=True)
        cmd.func = func
        return [cmd]


class ReverseProxied:
    """ Wrap the application in this middleware and configure the
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
