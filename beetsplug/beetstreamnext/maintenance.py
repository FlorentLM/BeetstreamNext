import threading
import time
from datetime import datetime

from .constants import CLEANUP_INTERVAL_SEC, MAX_CACHE_AGE_DAYS
from .application import app, rate_limiter


_cleanup_lock = threading.Lock()
_last_cleanup: float = 0.0


def run_periodic():
    """
    Runs housekeeping periodically.
    Deletes old cached thumbnails, purges rate limiting store.
    """

    global _last_cleanup

    now = time.time()
    if now - _last_cleanup < CLEANUP_INTERVAL_SEC:
        return

    if not _cleanup_lock.acquire(blocking=False):
        return  # another thread already doing it

    try:
        # check inside the lock if another thread may have just finished
        if now - _last_cleanup < CLEANUP_INTERVAL_SEC:
            return
        _last_cleanup = now
    finally:
        _cleanup_lock.release()

    def _background_maintenance():
        app.logger.info(f"[{datetime.fromtimestamp(now)}] Starting background maintenance...")

        rate_limiter.sweep()

        # Tidy cache
        cache_dir = app.config['THUMBNAIL_CACHE_PATH']
        if cache_dir.exists():
            max_age_seconds = MAX_CACHE_AGE_DAYS * 86400
            try:
                for f in cache_dir.iterdir():
                    if f.suffix == '.jpg' and (now - f.stat().st_mtime > max_age_seconds):
                        f.unlink(missing_ok=True)
            except Exception as e:
                app.logger.error(f"Error cleaning thumbnail cache: {e}")

        app.logger.info(f"[{datetime.fromtimestamp(now)}] Background maintenance complete.")

    thread = threading.Thread(target=_background_maintenance, daemon=True)
    thread.start()