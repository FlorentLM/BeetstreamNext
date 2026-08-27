import calendar
import os
from threading import Thread, Lock
import urllib.parse
from pathlib import Path
from typing import Optional

import flask

from beetsplug.beetstreamnext.application import app, with_app_context
from beetsplug.beetstreamnext.constants import (
    CACHE_LOCATION, FEEDPARSER, MAX_PODCAST_FEED_BYTES, MAX_PODCAST_IMAGE_DIM, USER_AGENT
)
from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.external import http_session, capped_image_fetch, normalize_url, https_variant
from beetsplug.beetstreamnext.core.images import resize_image, ImageTooLarge
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.settings import settings_store
from beetsplug.beetstreamnext.utils.text import parse_duration, strip_html


##
# Feed parsing helpers (these are stateless)

def _get_audio_url(entry) -> tuple[str, int]:

    for enc in entry.get('enclosures', []) or []:
        href = enc.get('href') or enc.get('url')
        enc_type = (enc.get('type') or '').lower()

        if href and (enc_type.startswith('audio/') or not enc_type):
            try:
                length = int(enc.get('length'))
            except (TypeError, ValueError):
                length = 0
            return href, length

    return '', 0


def _fetch_feed_bytes(url: str) -> bytes:

    # Feeds change quite frequently so no caching
    with http_session().cache_disabled():
        resp = http_session().get(url, stream=True, timeout=15, headers={'User-Agent': USER_AGENT})

    resp.raise_for_status()

    try:
        clen = resp.headers.get('Content-Length')
        if clen and clen.isdigit() and int(clen) > MAX_PODCAST_FEED_BYTES:
            raise ValueError(f'Feed too large ({clen} B, max {MAX_PODCAST_FEED_BYTES} B): {url}')

        buf = bytearray()
        for chunk in resp.iter_content(8192):
            buf += chunk
            if len(buf) > MAX_PODCAST_FEED_BYTES:
                raise ValueError(f'Feed exceeded {MAX_PODCAST_FEED_BYTES} B: {url}')
    finally:
        resp.close()

    return bytes(buf)


def _fetch_feed(url: str) -> tuple:
    """
    Fetches and parses url, trying to upgrade to https if possible.
    Returns the parsed feed, and the resolved url (the oen that actually worked).
    """

    import feedparser

    if url.lower().startswith('http://'):
        https_url = https_variant(url)
        try:
            return feedparser.parse(_fetch_feed_bytes(https_url)), https_url
        except Exception:
            pass   # fall through and try original http URL below

    return feedparser.parse(_fetch_feed_bytes(url)), url


##
#

class PodcastManager:

    def __init__(self):

        self._refresh_lock = Lock()
        self._download_lock = Lock()
        self._refreshing_channels: set = set()
        self._downloading_episodes: set = set()

    @staticmethod
    def storage_dir() -> Path:
        configured = settings_store.get('podcast_storage_dir')
        base = Path(configured) if configured else (CACHE_LOCATION / 'podcasts')
        base.mkdir(parents=True, exist_ok=True)
        return base

    def is_downloading(self, episode_id: Optional[int] = None) -> bool:
        with self._download_lock:
            return bool(self._downloading_episodes) if episode_id is None else episode_id in self._downloading_episodes

    # Channels

    @with_app_context
    def _worker_refresh_channel(self, channel_id: int, download_recents: bool = False, username: Optional[str] = None) -> None:
        """Worker for refreshing a single channel."""

        if not FEEDPARSER:
            bsn_logger.warning("Cannot refresh podcast channel: 'feedparser' is not installed.")
            return

        with self._refresh_lock:
            if channel_id in self._refreshing_channels:
                return
            self._refreshing_channels.add(channel_id)

        try:
            with database() as db:
                row = db.execute(
                    """
                    SELECT url, image_url 
                    FROM podcast_channels 
                    WHERE id = ?
                    """, (channel_id,)
                ).fetchone()

            if not row:
                return

            bsn_logger.info(f"Refreshing podcast channel {channel_id} ('{row['url']}')...")

            with database() as db:
                db.execute(
                    """
                    UPDATE podcast_channels 
                    SET status = 'downloading' 
                    WHERE id = ?
                    """, (channel_id,)
                )

            try:
                feed, resolved_url = _fetch_feed(row['url'])

                if resolved_url != row['url']:
                    try:
                        with database() as db:
                            db.execute(
                                """UPDATE podcast_channels SET url = ? WHERE id = ?""",
                                (resolved_url, channel_id)
                            )
                        bsn_logger.info(f"Upgraded podcast channel {channel_id} to '{resolved_url}'.")
                    except Exception as e:
                        bsn_logger.warning(f"Could not persist upgraded URL '{resolved_url}' for podcast channel {channel_id}: {e}")

                feed_info = feed.get('feed', {})
                title = feed_info.get('title') or row['url']
                description = strip_html(feed_info.get('subtitle') or feed_info.get('description') or '')

                image_url = (feed_info.get('image') or {}).get('href') or (feed_info.get('itunes_image') or {}).get('href') or ''

                image_bytes = None
                if image_url and image_url != row['image_url']:
                    fetched = capped_image_fetch(image_url)
                    if fetched:
                        try:
                            image_bytes = resize_image(fetched, MAX_PODCAST_IMAGE_DIM).getvalue()
                        except ImageTooLarge as e:
                            bsn_logger.warning(f"Cover image too large for podcast channel {channel_id}: '{image_url}' ({e})")
                    else:
                        bsn_logger.warning(f"Could not fetch cover image for podcast channel {channel_id}: '{image_url}'")

                episodes_added = 0
                episodes_skipped = 0

                with database() as db:
                    if image_bytes:
                        db.execute(
                            """
                            UPDATE podcast_channels
                            SET title = ?, description = ?, image = ?, image_url = ?
                            WHERE id = ?
                            """, (title, description, image_bytes, image_url, channel_id)
                        )
                    else:
                        db.execute(
                            """
                            UPDATE podcast_channels
                            SET title = ?, description = ?, image_url = ?
                            WHERE id = ?
                            """, (title, description, image_url, channel_id)
                        )

                    for entry in feed.get('entries', []):
                        # A malformed entry must not abort the whole channel refresh
                        try:
                            guid = entry.get('id') or entry.get('link') or entry.get('title')
                            if not guid:
                                episodes_skipped += 1
                                continue

                            audio_url, size = _get_audio_url(entry)

                            if not audio_url:
                                episodes_skipped += 1
                                continue

                            published_struct = entry.get('published_parsed')
                            publish_date = calendar.timegm(published_struct) if published_struct else None

                            db.execute(
                                """
                                INSERT INTO podcast_episodes
                                    (channel_id, guid, title, description, publish_date, audio_url, duration, file_size, status)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'new')
                                ON CONFLICT (channel_id, guid) DO UPDATE SET
                                    title        = excluded.title,
                                    description  = excluded.description,
                                    publish_date = excluded.publish_date,
                                    audio_url    = excluded.audio_url,
                                    duration     = excluded.duration
                                """, (
                                    channel_id, guid, entry.get('title') or '', strip_html(entry.get('summary') or ''),
                                    publish_date, audio_url, parse_duration(entry.get('itunes_duration')), size
                                )
                            )
                            episodes_added += 1

                        except Exception as e:
                            bsn_logger.warning(
                                f"Skipping unparsable episode in channel {channel_id} "
                                f"({entry.get('title', '<untitled>')!r}): {e}"
                            )
                            episodes_skipped += 1

                    db.execute(
                        """
                        UPDATE podcast_channels 
                        SET status = 'completed', error_message = NULL 
                        WHERE id = ?
                        """, (channel_id,)
                    )

                bsn_logger.info(
                    f'Refreshed podcast channel {channel_id}: got {episodes_added} episode(s)'
                    + (f', {episodes_skipped} skipped' if episodes_skipped else '') + '.'
                )

                if download_recents:
                    auto_count = settings_store.get('podcast_auto_download_count')
                    if auto_count > 0:
                        with database() as db:
                            episode_ids = [
                                r['id'] for r in db.execute(
                                    """
                                    SELECT id FROM podcast_episodes
                                    WHERE channel_id = ?
                                    ORDER BY publish_date DESC
                                    LIMIT ?
                                    """, (channel_id, auto_count)
                                ).fetchall()
                            ]
                        if episode_ids:
                            bsn_logger.info(
                                f'Auto-downloading {len(episode_ids)} most recent episode(s) '
                                f'for newly-added podcast channel {channel_id}...'
                            )
                            for ep_id in episode_ids:
                                if username:
                                    self._record_want(username, ep_id)
                                self.background_download(ep_id)

            except Exception as e:
                bsn_logger.error(f"Failed to refresh podcast channel {channel_id} ('{row['url']}'): {e}")
                with database() as db:
                    db.execute(
                        """
                        UPDATE podcast_channels 
                        SET status = 'error', error_message = ? 
                        WHERE id = ?
                        """, (str(e), channel_id)
                    )
        finally:
            with self._refresh_lock:
                self._refreshing_channels.discard(channel_id)

    @with_app_context
    def refresh(self, channel_id: Optional[int] = None, download_recents: bool = False, username: Optional[str] = None) -> None:

        if not FEEDPARSER:
            return

        if channel_id is None:
            with database() as db:
                rows = db.execute(
                    """
                    SELECT id
                    FROM podcast_channels
                    """
                ).fetchall()

            for row in rows:
                self._worker_refresh_channel(channel_id=row['id'], download_recents=download_recents, username=username)

        else:
            self._worker_refresh_channel(channel_id=channel_id, download_recents=download_recents, username=username)

    def background_refresh(self, channel_id: Optional[int] = None, download_recents: bool = False, username: Optional[str] = None) -> None:

        thread = Thread(
            target=self.refresh,
            args=(channel_id,),
            kwargs={'download_recents': download_recents, 'username': username},
            daemon=True
        )
        thread.start()

    def create_channel(self, username: str, url: str) -> tuple[Optional[int], Optional[str]]:
        """
        Creates the channel row if its URL is new, or just subscribes username to the
        existing shared channel.

        url is normalised first and deduped against its http/https variants.

        Returns (channel_id, error_message), channel_id can be None if a new channel's feed
        could not be fetched.
        """

        url = normalize_url(url)
        alt_url = https_variant(url)

        with database() as db:
            existing = db.execute(
                """
                SELECT id 
                FROM podcast_channels 
                WHERE url = ? OR url = ?
                """, (url, alt_url)
            ).fetchone()

            if existing:
                channel_id = existing['id']
                is_new = False
            else:
                cur = db.execute(
                    """
                    INSERT INTO podcast_channels (url, title, status)
                    VALUES (?, ?, 'new')
                    ON CONFLICT (url) DO NOTHING
                    """, (url, url)
                )
                if cur.rowcount > 0:
                    channel_id = cur.lastrowid
                    is_new = True
                else:
                    # Another request for the same URL already did it
                    channel_id = db.execute(
                        """
                        SELECT id 
                        FROM podcast_channels 
                        WHERE url = ?
                        """, (url,)
                    ).fetchone()['id']
                    is_new = False

            db.execute(
                """
                INSERT INTO podcast_subscriptions (username, channel_id)
                VALUES (?, ?)
                ON CONFLICT (username, channel_id) DO NOTHING
                """, (username, channel_id)
            )

        if is_new:
            self.refresh(channel_id, download_recents=True, username=username)

            with database() as db:
                row = db.execute(
                    """
                    SELECT status, error_message 
                    FROM podcast_channels WHERE id = ?
                    """, (channel_id,)
                ).fetchone()

            if row and row['status'] == 'error':
                self.delete_channel(channel_id)
                return None, row['error_message']

        return channel_id, None

    def unsubscribe(self, username: str, channel_id: int, force: bool = False) -> None:
        """
        Removes username's subscription to channel_id. The shared channel/episodes/files are
        only purged when no subscriber remains (unless forced by admin override).
        """

        with database() as db:
            db.execute(
                """
                DELETE FROM podcast_subscriptions 
                WHERE username = ? AND channel_id = ?
                """, (username, channel_id)
            )
            remaining = db.execute(
                """
                SELECT COUNT(*) AS n 
                FROM podcast_subscriptions 
                WHERE channel_id = ?
                """, (channel_id,)
            ).fetchone()['n']

        if force or remaining == 0:
            self.delete_channel(channel_id)

    def delete_channel(self, channel_id: int) -> None:
        """Unconditionally purges a channel, episodes and files for all subscribers."""

        with database() as db:
            rows = db.execute(
                """
                SELECT file_path
                FROM podcast_episodes
                WHERE channel_id = ? AND file_path IS NOT NULL
                """, (channel_id,)
            ).fetchall()

            db.execute(
                """
                DELETE FROM podcast_channels
                WHERE id = ?
                """, (channel_id,)
            )   # Cascade to episodes, subscriptions and episode download wants

        for row in rows:
            try:
                Path(row['file_path']).unlink(missing_ok=True)
            except OSError as e:
                bsn_logger.warning(f"Failed to remove podcast episode file '{row['file_path']}': {e}")

    # Episodes

    def _record_want(self, username: str, episode_id: int) -> None:
        with database() as db:
            db.execute(
                """
                INSERT INTO podcast_episode_downloads (username, episode_id)
                VALUES (?, ?)
                ON CONFLICT (username, episode_id) DO NOTHING
                """, (username, episode_id)
            )

    def _want_count(self, episode_id: int) -> int:
        with database() as db:
            return db.execute(
                """
                SELECT COUNT(*) AS n 
                FROM podcast_episode_downloads 
                WHERE episode_id = ?
                """, (episode_id,)
            ).fetchone()['n']

    def _reserve_download(self, episode_id: int) -> bool:
        """
        Mark episodes for download atomically.
        Returns False if a download is already underway.
        """
        with self._download_lock:
            if episode_id in self._downloading_episodes:
                return False
            self._downloading_episodes.add(episode_id)

        with database() as db:
            db.execute(
                """
                UPDATE podcast_episodes 
                SET status = 'downloading', error_message = NULL 
                WHERE id = ?
                """, (episode_id,)
            )
        return True

    @with_app_context
    def _worker_download_episode(self, episode_id: int, channel_id: int, audio_url: str) -> None:
        """
        Worker for getting and writing an episode's audio to disk.
        """
        bsn_logger.info(f"Downloading podcast episode {episode_id} from '{audio_url}'...")

        tmp_path = None
        try:
            try:
                channel_dir = self.storage_dir() / str(channel_id)
                channel_dir.mkdir(parents=True, exist_ok=True)

                ext = Path(urllib.parse.urlparse(audio_url).path).suffix or '.mp3'
                target_path = channel_dir / f'{episode_id}{ext}'
                tmp_path = target_path.with_suffix(target_path.suffix + '.part')

                # Never cache the audio files
                with http_session().cache_disabled():
                    resp = http_session().get(audio_url, stream=True, timeout=30, headers={'User-Agent': USER_AGENT})
                    resp.raise_for_status()

                    size = 0
                    with open(tmp_path, 'wb') as f:
                        for chunk in resp.iter_content(65536):
                            f.write(chunk)
                            size += len(chunk)

                os.replace(tmp_path, target_path)

            except Exception as e:
                bsn_logger.warning(f'Failed to download podcast episode {episode_id}: {e}')

                if tmp_path is not None:
                    Path(tmp_path).unlink(missing_ok=True)

                with database() as db:
                    db.execute(
                        """
                        UPDATE podcast_episodes 
                        SET status = 'error', error_message = ? 
                        WHERE id = ?
                        """, (str(e), episode_id)
                    )

                return

            with database() as db:
                db.execute(
                    """
                    UPDATE podcast_episodes
                    SET status = 'completed', file_path = ?, file_size = ?, error_message = NULL
                    WHERE id = ?
                    """, (str(target_path), size, episode_id)
                )

            bsn_logger.info(f'Downloaded podcast episode {episode_id} ({size} bytes) to {target_path}')

        finally:
            with self._download_lock:
                self._downloading_episodes.discard(episode_id)

    @with_app_context
    def download(self, episode_id: int) -> None:
        """
        Reserve and download an episode to server storage, sequential/blocking, for background.
        """

        with database() as db:
            row = db.execute(
                """
                SELECT channel_id, audio_url 
                FROM podcast_episodes 
                WHERE id = ?
                """, (episode_id,)
            ).fetchone()

        if not row or not row['audio_url']:
            bsn_logger.warning(f"Podcast episode {episode_id} has no known audio URL, can't download.")
            return

        if not self._reserve_download(episode_id):
            return   # already downloading

        self._worker_download_episode(episode_id, row['channel_id'], row['audio_url'])

    def background_download(self, episode_id: int) -> bool:
        """
        Reserve and download an episode to server storage, synchronously, for route handlers.
        (when this returns, the DB shows 'downloading' or it already reached 'completed' on its own).

        Actual fetch + write happens in the background thread.

        Returns True if the episode is downloading, returns False only when it can't be downloaded at all.
        """

        with self._download_lock:
            if episode_id in self._downloading_episodes:
                return True

        with database() as db:
            row = db.execute(
                """
                SELECT channel_id, audio_url 
                FROM podcast_episodes 
                WHERE id = ?
                """, (episode_id,)
            ).fetchone()

        if not row or not row['audio_url']:
            return False

        if not self._reserve_download(episode_id):
            return True   # was claimed between the check above and here, that's fine

        thread = Thread(
            target=self._worker_download_episode,
            args=(episode_id, row['channel_id'], row['audio_url']),
            daemon=True
        )
        thread.start()

        return True

    def request_episode_download(self, username: str, episode_id: int) -> bool:
        """
        Records that username wants episode_id downloaded, and makes sure the audio file
        is being fetched (if not already on disk).

        Returns False only when the episode has no known audio source.
        """

        self._record_want(username, episode_id)

        with database() as db:
            row = db.execute(
                """
                SELECT status, audio_url 
                FROM podcast_episodes 
                WHERE id = ?
                """, (episode_id,)
            ).fetchone()

        if not row or not row['audio_url']:
            return False

        if row['status'] == 'completed':
            return True

        return self.background_download(episode_id)

    def release_episode_download(self, username: str, episode_id: int) -> None:
        """
        Clears username's want for episode_id. Once no subscriber wants it downloaded anymore,
        the audio file is purged from disk (metadata stays, ofc).
        """

        with database() as db:
            db.execute(
                """
                DELETE FROM podcast_episode_downloads 
                WHERE username = ? AND episode_id = ?
                """, (username, episode_id)
            )

        if self._want_count(episode_id) > 0:
            return

        with database() as db:
            row = db.execute(
                """
                SELECT file_path 
                FROM podcast_episodes 
                WHERE id = ?
                """, (episode_id,)
            ).fetchone()

            if not row or not row['file_path']:
                return

            db.execute(
                """
                UPDATE podcast_episodes
                SET status = 'new', file_path = NULL, file_size = NULL, error_message = NULL
                WHERE id = ?
                """, (episode_id,)
            )

        try:
            Path(row['file_path']).unlink(missing_ok=True)
        except OSError as e:
            bsn_logger.warning(f"Failed to remove podcast episode file '{row['file_path']}': {e}")

    def relayed_download(self, episode_id: int, channel_id: int, audio_url: str):
        """
        Starts a streamed HTTP GET for an episode that isn't downloaded yet.
        Reserves its disk paths and marks its status to 'downloading'.
        
        The routes (/stream and /download) relay the response body to the client while writing it to tmp_path,
        and then call finish_relayed_download() at the end.

        Returns None if a download for this episode is already underway (via this or another route).
        """

        with self._download_lock:
            if episode_id in self._downloading_episodes:
                return None
            self._downloading_episodes.add(episode_id)

        try:
            with database() as db:
                db.execute(
                    """
                    UPDATE podcast_episodes 
                    SET status = 'downloading', error_message = NULL 
                    WHERE id = ?
                    """, (episode_id,)
                )

            channel_dir = self.storage_dir() / str(channel_id)
            channel_dir.mkdir(parents=True, exist_ok=True)

            ext = Path(urllib.parse.urlparse(audio_url).path).suffix or '.mp3'
            target_path = channel_dir / f'{episode_id}{ext}'
            tmp_path = target_path.with_suffix(target_path.suffix + '.part')

            # Never cache the audio files
            with http_session().cache_disabled():
                resp = http_session().get(audio_url, stream=True, timeout=30, headers={'User-Agent': USER_AGENT})

            resp.raise_for_status()

            return resp, tmp_path, target_path

        except Exception:

            with database() as db:
                db.execute(
                    """
                    UPDATE podcast_episodes 
                    SET status = 'new', error_message = NULL 
                    WHERE id = ?
                    """, (episode_id,)
                )

            with self._download_lock:
                self._downloading_episodes.discard(episode_id)

            raise

    def finish_relayed_download(self, episode_id: int, tmp_path: Path, target_path: Path, size: int, success: bool) -> None:
        """Called once the streaming route is done relaying bytes to the client, successfully or not."""

        try:
            if success:
                os.replace(tmp_path, target_path)

                with database() as db:
                    db.execute(
                        """
                        UPDATE podcast_episodes
                        SET status = 'completed', file_path = ?, file_size = ?, error_message = NULL
                        WHERE id = ?
                        """, (str(target_path), size, episode_id)
                    )

                bsn_logger.info(f'Cached podcast episode {episode_id} while streaming it ({size} bytes)')

            else:
                Path(tmp_path).unlink(missing_ok=True)

                with database() as db:
                    db.execute(
                        """
                        UPDATE podcast_episodes 
                        SET status = 'new', error_message = NULL 
                        WHERE id = ?
                        """, (episode_id,)
                    )

                bsn_logger.warning(f'Streaming podcast episode {episode_id} was interrupted before the download finished.')

        except Exception as e:
            bsn_logger.error(f'Failed to finalize live-streamed podcast episode {episode_id}: {e}')

        finally:
            with self._download_lock:
                self._downloading_episodes.discard(episode_id)

    def delete_episode(self, episode_id: int) -> None:
        """
        Unconditionally removes an episode's audio file all subscribers (admin override),
        keeping its metadata (status becomes 'deleted').
        """

        with database() as db:
            row = db.execute(
                """
                SELECT file_path
                FROM podcast_episodes
                WHERE id = ?
                """, (episode_id,)
            ).fetchone()

            if not row:
                return

            db.execute(
                """
                UPDATE podcast_episodes
                SET status = 'deleted', file_path = NULL, error_message = NULL
                WHERE id = ?
                """, (episode_id,)
            )
            db.execute(
                """
                DELETE FROM podcast_episode_downloads 
                WHERE episode_id = ?
                """, (episode_id,)
            )

        if row['file_path']:
            try:
                Path(row['file_path']).unlink(missing_ok=True)
            except OSError as e:
                bsn_logger.warning(f"Failed to remove podcast episode file '{row['file_path']}': {e}")
