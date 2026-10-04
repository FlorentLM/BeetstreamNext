"""
Throwaway sandbox and dummy data, used by the test suite and the docs screenshots
(nothing here should import beetsplug because use_sandbox() must run first)
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

ADMIN_USER, ADMIN_PASSWORD = 'admin', 'hunter2?'

# (album, albumartist, year, track titles)
CORE_ALBUMS = [
    ('Abbey Road', 'The Beatles', 1969, ['Come Together', 'Something']),
    ('Kind of Blue', 'Miles Davis', 1959, ['So What']),
]

EXTRA_ALBUMS = [
    ('Rumours', 'Fleetwood Mac', 1977, ['Dreams', 'Go Your Own Way']),
    ('Random Access Memories', 'Daft Punk', 2013, ['Get Lucky', 'Instant Crush']),
]

# (username, password), the admin is created separately
USERS = [('alice', 'alice-password'), ('bob', 'bob-password')]

RADIOS = [      # (name, stream url, homepage)
    ('Radio Paradise', 'https://stream.radioparadise.com/mp3-320', 'https://radioparadise.com'),
    ('SomaFM Groove Salad', 'https://ice1.somafm.com/groovesalad-128-mp3', 'https://somafm.com/groovesalad'),
    ('KEXP 90.3 FM', 'https://kexp.streamguys1.com/kexp160.aac', 'https://www.kexp.org'),
]

PODCASTS = [
    ('The Music Lab', 'Weekly deep dives into how records get made.', [
        ('Ep. 42: Recording to tape', 3), ('Ep. 41: The art of mixing', 10), ('Ep. 40: Sampling, legally', 17),
    ]),
    ('Liner Notes', 'Stories behind classic albums.', [
        ('Kind of Blue at 65', 2), ('Making Rumours', 9),
    ]),
]

CHAT = [
    ('Server', 'Maintenance window on Sunday at 03:00.'),
    ('alice', 'There is a new Daft Punk album??'),
    ('bob', 'Yes! Finally!'),
]

PINNED_FOLDERS = [
    ('downloads', False),
    ('bandcamp', True),
]


def use_sandbox() -> Path:
    """
    Importing beetsplug.beetstreamnext creates cache/config/data dirs as a side effect:
        -> redirect all to a throwaway directory before anything is imported
    """
    sandbox = Path(tempfile.mkdtemp(prefix='bsn-sandbox-'))

    os.environ['HOME'] = str(sandbox)
    os.environ['XDG_CONFIG_HOME'] = str(sandbox / 'config')
    os.environ['XDG_CACHE_HOME'] = str(sandbox / 'cache')
    os.environ['APPDATA'] = str(sandbox / 'appdata')
    os.environ['LOCALAPPDATA'] = str(sandbox / 'localappdata')
    os.environ.pop('BSN_IN_DOCKER', None)
    os.environ['BEETSTREAMNEXT_KEY'] = 'Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyMTI='     # fixed Fernet key, demo/test-only
    return sandbox


def build_app(root: Path, config: dict | None = None, yaml_defaults: dict | None = None):
    """
    The real Flask app wired to the throwaway beets library and BeetstreamNext db.
    """
    from beets.library import Library
    from beetsplug.beetstreamnext import app
    from beetsplug.beetstreamnext.core.runtime.startup import prestartup_config
    from beetsplug.beetstreamnext.core.storage.schema import initialise_db
    from beetsplug.beetstreamnext.core.accounts.users_crud import create_user
    from beetsplug.beetstreamnext.core.media.playlists import PlaylistProvider
    from beetsplug.beetstreamnext.core.services.podcasts import PodcastManager
    from beetsplug.beetstreamnext.core.config.store import settings_store

    music_dir = root / 'music'
    music_dir.mkdir()

    beets_db = root / 'library.db'
    lib = Library(str(beets_db), directory=str(music_dir))

    prestartup_config(beets_db, root / 'bsn.db', None)

    app.config.update(lib=lib, root_directory=music_dir, **(config or {}))
    app.config.pop('_users_exist_cache', None)
    playlist_dir = root / 'playlists'
    playlist_dir.mkdir()
    app.config['playlist_dirs'] = {0: playlist_dir, 1: None, 2: None}
    app.config['playlist_provider'] = PlaylistProvider()
    app.config['podcast_manager'] = PodcastManager()

    with app.app_context():
        initialise_db()
        settings_store.initialise(yaml_defaults={'fetch_radio_images': False, **(yaml_defaults or {})})
        create_user(ADMIN_USER, ADMIN_PASSWORD, admin=True)

    return app, lib


def add_albums(lib, specs=CORE_ALBUMS) -> dict:
    """
    Adds albums to the dummy beets library (audio files don't exist on disk).
    """
    from beets.library import Item

    music = Path(lib.directory.decode() if isinstance(lib.directory, bytes) else lib.directory)
    albums = {}

    for album_title, albumartist, year, tracks in specs:
        items = []
        for n, title in enumerate(tracks, 1):
            path = music / albumartist / album_title / f'{n:02d} {title}.mp3'
            item = Item(
                title=title, artist=albumartist, albumartist=albumartist, album=album_title,
                track=n, tracktotal=len(tracks), year=year, genres=['Rock'], length=180.0 + n,
                bitrate=320000, format='MP3', path=str(path).encode(),
            )
            lib.add(item)
            items.append(item)
        albums[album_title] = lib.add_album(items)

    return albums


def seed_demo(app, lib, import_root: Path | None = None) -> None:
    """
    Fills the rest of the app with dummy data: extra albums, users, radios, podcasts,
    chat messages and pinned import folders
    """
    from beetsplug.beetstreamnext.core.storage.connection import database
    from beetsplug.beetstreamnext.core.accounts.users_crud import create_user

    add_albums(lib, EXTRA_ALBUMS)

    now = time.time()
    with app.app_context():
        for username, password in USERS:
            create_user(username, password, admin=False)

        with database() as db:
            for name, stream, home in RADIOS:
                db.execute("INSERT INTO internet_radio_stations (name, stream_url, homepage_url) VALUES (?, ?, ?)",
                           (name, stream, home))

            for i, (title, desc, episodes) in enumerate(PODCASTS, 1):
                cur = db.execute(
                    "INSERT INTO podcast_channels (url, title, description, status) VALUES (?, ?, ?, 'completed')",
                    (f'https://example.org/feed{i}.xml', title, desc))

                for j, (ep_title, days_ago) in enumerate(episodes):
                    db.execute(
                        """
                        INSERT INTO podcast_episodes (channel_id, guid, title, description, publish_date, audio_url, duration, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'new')
                        """, (cur.lastrowid, f'{i}-{j}', ep_title, desc, now - days_ago * 86400,
                              f'https://example.org/{i}-{j}.mp3', 2400 + 120 * j))

            for k, (user, message) in enumerate(CHAT):
                db.execute("INSERT INTO chat_messages (username, time, message) VALUES (?, ?, ?)",
                           (user, (now - (len(CHAT) - k) * 600) * 1000, message))

            if import_root is not None:
                for folder, watched in PINNED_FOLDERS:
                    (import_root / folder).mkdir(parents=True, exist_ok=True)
                    db.execute("INSERT INTO pinned_import_paths (path, incremental, watch) VALUES (?, 1, ?)",
                               (str(import_root / folder), int(watched)))
