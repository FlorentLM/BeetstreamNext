from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from beetsplug.beetstreamnext.application import app, with_app_context
from beetsplug.beetstreamnext.core.library.indexes import ArtistIndex
from beetsplug.beetstreamnext.core.media.artist_images import (
    fetched_image_path, load_stored, record_status
)
from beetsplug.beetstreamnext.core.media.images import safe_open_image
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.services.events import admin_events
from beetsplug.beetstreamnext.core.services.external.deezer import query_deezer, DeezerUnavailable
from beetsplug.beetstreamnext.core.services.external.session import capped_image_fetch
from beetsplug.beetstreamnext.utils.concurrency import SingleAccess


PICTURE_FIELDS = ('picture_xl', 'picture_big', 'picture_medium', 'picture', 'picture_small')


@dataclass(frozen=True)
class LookupResult:

    status: str
    name: str = ''
    matched_name: str | None = None
    data: dict | None = None        # Deezer payload of the match
    failed: bool = False

    @property
    def found(self) -> bool:
        return self.status in ('deezer', 'fallback')


@dataclass
class FetchProgress:
    total: int = 0
    done: int = 0
    found: int = 0
    fallback: int = 0
    missing: int = 0
    aborted: str = ''
    finished: bool = False


class ArtistImageFetcher:
    """
    Fetches artist images from Deezer: one artist (on demand), or all artists (in a background run).
    """

    RETRY_AFTER = 30 * 24 * 3600        # Seconds before retrying an artist
    LOOKUP_PAUSE = 0.25                 # Seconds between Deezer requests in a background run
    MAX_CONSECUTIVE_FAILURES = 5
    PUBLISH_EVERY = 5                   # Progress reported every n artists

    def __init__(self) -> None:
        self._lock = SingleAccess()
        self.progress = FetchProgress()

    @property
    def enabled(self) -> bool:
        return bool(app.config['fetch_artists_images'])

    @property
    def running(self) -> bool:
        return self._lock.running

    def lookup(self,
            key: str,
            name: str,
            candidates: tuple[str, ...],
            pause: float = 0.0,
            overwrite: bool = False
        ) -> LookupResult:
        """
        Try each candidate on Deezer, save the image of the first match and record the outcome.
        """
        match: tuple[str, dict] | None = None
        failed = False

        for candidate in candidates:
            try:
                data = query_deezer(artist=candidate)
            except DeezerUnavailable:
                failed = True
                continue
            finally:
                if pause:
                    time.sleep(pause)

            if data and data.get('type', '') == 'artist':
                match = candidate, data
                break

        if match:
            matched_name, data = match
            result = LookupResult('deezer' if matched_name == name else 'fallback', name, matched_name, data, failed)
            self._save_image(name, data, overwrite)
        else:
            result = LookupResult('unavailable' if failed else 'missing', name, failed=failed)

        if not failed:
            try:
                record_status(key, name, 'deezer' if result.found else 'none', result.matched_name)
            except Exception as e:
                bsn_logger.warning(f"Could not record image status of '{name}': {e}")

        return result

    @staticmethod
    def _save_image(name: str, data: dict, overwrite: bool) -> None:

        path = fetched_image_path(name)
        if path.is_file() and not overwrite:
            return

        field_name = next(filter(data.get, PICTURE_FIELDS), None)
        if not field_name:
            return

        try:
            content = capped_image_fetch(str(data[field_name]), timeout=5)
            if content:
                safe_open_image(content).save(path)
        except Exception as e:
            bsn_logger.warning(f"Failed to fetch/save artist image for '{name}' from Deezer: {e}")

    @with_app_context
    def fetch_one(self, name: str, artist_id: str = '') -> LookupResult:
        """
        Fetches (or refreshes) the image of one library artist.
        """
        if not self.enabled:
            return LookupResult('disabled')

        index = ArtistIndex.get()
        # Deezer lookups are by name
        artist = index.lookup(artist_id or name) or next(iter(index.named(name)), None)
        if not artist:
            return LookupResult('unknown')

        result = self.lookup(artist.keys[0], artist.name, artist.candidates, overwrite=True)
        admin_events.publish('artist-fetch-done', '')
        return result

    @with_app_context
    def _worker_fetch_all(self) -> None:
        progress = self.progress = FetchProgress()

        try:
            stored = load_stored()
            cutoff = time.time() - self.RETRY_AFTER
            todo = []

            for artist in ArtistIndex.get().album_artists:

                row = next((stored[k] for k in artist.keys if k in stored), None)
                if row and (row['has_image'] or (row['checked_at'] or 0) > cutoff):
                    continue

                todo.append(artist)

            progress.total = len(todo)
            admin_events.publish('artist-fetch', '')

            failures = 0
            for artist in todo:
                result = self.lookup(artist.keys[0], artist.name, artist.candidates, pause=self.LOOKUP_PAUSE)

                if result.failed:
                    failures += 1
                    if failures >= self.MAX_CONSECUTIVE_FAILURES:
                        progress.aborted = 'Deezer is not responding.'
                        break
                else:
                    failures = 0
                    if result.status == 'deezer':
                        progress.found += 1
                    elif result.status == 'fallback':
                        progress.fallback += 1
                    else:
                        progress.missing += 1

                progress.done += 1
                if progress.done % self.PUBLISH_EVERY == 0:
                    admin_events.publish('artist-fetch', '')

            bsn_logger.info(
                f"Artist image fetch complete: {progress.done}/{progress.total} artists, {progress.found} found on Deezer, "
                f"{progress.fallback} using a member's image, {progress.missing} missing."
                + (f" Stopped: {progress.aborted}" if progress.aborted else '')
            )
        except Exception as e:
            progress.aborted = 'Failed, see the server log.'
            bsn_logger.error(f"Artist image fetch failed: {e}")
        finally:
            progress.finished = True
            self._lock.release()
            admin_events.publish('artist-fetch', '')
            admin_events.publish('artist-fetch-done', '')

    def fetch_all(self) -> tuple[bool, str]:
        """
        Background run to fetch all missing artist images (and not tried recently).
        """
        if not self.enabled:
            return False, 'Fetching is off in the settings.'

        if not self._lock.try_acquire():
            return False, 'Already running.'

        threading.Thread(target=self._worker_fetch_all, daemon=True).start()
        return True, 'Started.'


artist_image_fetcher = ArtistImageFetcher()
