from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, ClassVar

from beetsplug.beetstreamnext.application import with_app_context
from beetsplug.beetstreamnext.constants import HISTORY_DUPLICATE_WINDOW_SEC
from beetsplug.beetstreamnext.core.accounts.users_crud import get_userdata
from beetsplug.beetstreamnext.core.config.store import settings_store
from beetsplug.beetstreamnext.core.library.beets.interaction import commit_likes
from beetsplug.beetstreamnext.core.library.indexes import SongIndex
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.services.scrobbling import SCROBBLERS
from beetsplug.beetstreamnext.core.services.scrobbling.base import ScrobbleError, ScrobblerBase
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.core.storage.encryption import decrypt_json
from beetsplug.beetstreamnext.core.storage.userprefs_cache import touch_change
from beetsplug.beetstreamnext.utils.concurrency import KeyedLimiter, WorkerPool


@dataclass
class PlayHistoryImporter:
    username: str
    scrobbler: ScrobblerBase
    feedback: bool              # Pulling likes and dislikes
    listens: bool               # Pulling listens
    running: bool = True
    feedback_read: int = 0
    likes_added: int = 0
    dislikes_added: int = 0
    listens_read: int = 0
    plays_added: int = 0
    unmatched: int = 0          # Not found in the library
    note: str = ''              # Why something is not imported
    error: str = ''

    workers: ClassVar = WorkerPool(1, name='history-import')
    latest: ClassVar[dict[tuple[str, str], PlayHistoryImporter]] = {}    # (user, service) -> running or last import
    _running: ClassVar = KeyedLimiter(1)                            # One import per (user, service)

    @classmethod
    def start(cls, username: str, service: str) -> str:

        scrobbler = SCROBBLERS.get(service)

        capabilities = scrobbler.capabilities if scrobbler else frozenset()
        if not capabilities & {'pull_feedback', 'pull_listens'}:
            return 'This service cannot import.'

        with database() as db:
            conn = db.execute(
                """
                SELECT display_name, last_error, credentials_encrypted
                FROM user_connections
                WHERE username = ? AND service = ?
                """, (username, service)
            ).fetchone()

        if conn is None:
            return f'Connect {scrobbler.label} first.'
        if conn['last_error']:
            return f'Reconnect {scrobbler.label} first.'
        if not conn['display_name']:
            return f'{scrobbler.label} did not say which account the token belongs to, reconnect it.'

        if not cls._running.try_acquire((username, service)):
            return 'An import is already running.'

        history_on = (get_userdata(username, fields=['playHistoryEnabled']) or {}).get('playHistoryEnabled', 1) != 0
        job = cls(username, scrobbler, feedback='pull_feedback' in capabilities,
                  listens='pull_listens' in capabilities and history_on)
        if 'pull_listens' in capabilities and not history_on:
            job.note = 'Listens are skipped because your play history is off.'

        cls.latest[(username, service)] = job
        cls.workers.submit(job.run, decrypt_json(conn['credentials_encrypted']), conn['display_name'])

        return ''

    @with_app_context
    def run(self, credentials: dict[str, Any], account: str) -> None:

        username, service = self.username, self.scrobbler.service
        index = SongIndex.get()

        try:
            if self.feedback:
                # Pulled first so no transaction stays open during network calls
                pulled = []
                for entry in self.scrobbler.pull_feedback(credentials, account):
                    self.feedback_read += 1
                    song_id = index.find(entry.recording_mbid, entry.artist, entry.title, entry.album)

                    if song_id is None:
                        self.unmatched += 1
                    else:
                        pulled.append((song_id, entry.score, entry.at or time.time()))

                liked = []

                with database() as db:
                    for song_id, score, at in pulled:

                        known = db.execute(
                            """
                            SELECT 1 FROM likes WHERE username = ? AND item_id = ?
                            UNION ALL
                            SELECT 1 FROM dislikes WHERE username = ? AND item_id = ?
                            """, (username, song_id, username, song_id)
                        ).fetchone()
                        if known:
                            continue

                        if score == 1:
                            db.execute(
                                """
                                INSERT INTO likes (username, item_id, starred_at)
                                VALUES (?, ?, ?)
                                """, (username, song_id, at)
                            )
                            liked.append(song_id)
                        else:
                            db.execute(
                                """
                                INSERT INTO dislikes (username, item_id, disliked_at)
                                VALUES (?, ?, ?)
                                """, (username, song_id, at)
                            )
                            self.dislikes_added += 1

                    if liked or self.dislikes_added:
                        touch_change(db, username, 'exclusions')

                self.likes_added = len(liked)
                if username == settings_store.get('ratings_writeback_user'):
                    for song_id in liked:
                        commit_likes(song_id, 'starred', 1)

            if self.listens:
                before = int(time.time()) + 1

                while page := self.scrobbler.pull_listens(credentials, account, before):
                    self.listens_read += len(page)

                    with database() as db:

                        touched = set()

                        for listen in page:
                            song_id = index.find(listen.recording_mbid, listen.artist, listen.title, listen.album)
                            if song_id is None:
                                self.unmatched += 1
                                continue

                            # Known play close in time = the same play
                            inserted = db.execute(
                                """
                                INSERT OR IGNORE INTO play_history (username, song_id, played_at, source)
                                SELECT ?, ?, ?, ?
                                WHERE NOT EXISTS (
                                    SELECT 1 FROM play_history
                                    WHERE username = ? AND song_id = ? AND played_at > ? AND played_at < ?
                                )
                                """, (username, song_id, listen.at, service, username, song_id,
                                      listen.at - HISTORY_DUPLICATE_WINDOW_SEC, listen.at + HISTORY_DUPLICATE_WINDOW_SEC)
                            ).rowcount

                            if inserted:
                                touched.add(song_id)
                                self.plays_added += 1

                        # Plays counted before history existed have no row
                        db.executemany(
                            """
                            INSERT INTO play_stats (username, song_id, play_count, last_played)
                            SELECT username, song_id, COUNT(*), MAX(played_at)
                            FROM play_history
                            WHERE username = ? AND song_id = ?
                            GROUP BY song_id
                            ON CONFLICT (username, song_id) DO UPDATE SET
                                play_count  = MAX(play_count, excluded.play_count),
                                last_played = MAX(COALESCE(last_played, 0), excluded.last_played)
                            """, [(username, song_id) for song_id in touched]
                        )

                    oldest = min(listen.at for listen in page)
                    if oldest >= before:
                        break       # The service ignored `before` so dont loop forever
                    before = oldest

        except ScrobbleError as e:
            self.error = str(e)

        except Exception as e:
            bsn_logger.warning(f"Importing the {service} history of '{username}' failed: {type(e).__name__}")
            self.error = 'The import failed, see the server log.'

        finally:
            self.running = False

            self._running.release((username, service))

            bsn_logger.info(
                f"'{username}' imported from {service}: {self.plays_added} play(s), "
                f"{self.likes_added} like(s), {self.dislikes_added} dislike(s)"
                + (f' (stopped: {self.error})' if self.error else '')
            )
