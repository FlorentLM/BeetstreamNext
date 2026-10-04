from __future__ import annotations

import time
from typing import Optional, List, Tuple

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.core.services.external.radio_browser import query_radio_browser, fetch_favicon
from beetsplug.beetstreamnext.core.services.external.session import capped_image_fetch, normalize_url
from beetsplug.beetstreamnext.core.media.images import sniff_image
from beetsplug.beetstreamnext.utils.net import https_variant


def resolve_station_icon(name: str, favicon_url: Optional[str] = None, homepage_url: Optional[str] = None) -> bytes:
    """
    Station icon fetch, in order: the given favicon URL, a Radio Browser lookup (by name),
    then the homepage's favicon.
    """
    image = b''

    if favicon_url:
        image = capped_image_fetch(favicon_url)
        if image and not sniff_image(image):
            image = b''

    if not image:
        resp = query_radio_browser(name, limit=1)
        if resp and resp[0].get('favicon'):
            image = capped_image_fetch(resp[0]['favicon'])
            if image and not sniff_image(image):
                image = b''

    if not image and homepage_url:
        image = fetch_favicon(homepage_url)
        if image and not sniff_image(image):
            image = b''

    return image


def create_station(
        name: str,
        stream_url: str,
        homepage_url: Optional[str] = None,
        image: Optional[bytes] = None,
        favicon_url: Optional[str] = None,
    ) -> Tuple[int | None, str | None]:
    """
    Returns (station_id, error_message).
    station_id is None if the stream URL is already registered
    """

    stream_url = normalize_url(stream_url, probe_https=True)
    if homepage_url:
        homepage_url = normalize_url(homepage_url)

    with database() as db:
        existing = db.execute(
            """
            SELECT name
            FROM internet_radio_stations
            WHERE stream_url = ? OR stream_url = ?
            """, (stream_url, https_variant(stream_url))
        ).fetchone()

    if existing:
        return None, f"A station with this stream URL already exists ('{existing['name']}')."

    if not image and app.config.get('fetch_radio_images'):
        image = resolve_station_icon(name, favicon_url, homepage_url) or None

    with database() as db:
        cur = db.execute(
            """
            INSERT INTO internet_radio_stations (name, stream_url, homepage_url, image, image_mtime)
            VALUES (?, ?, ?, ?, ?)
            """, (name, stream_url, homepage_url, image, time.time() if image else None)
        )

    return cur.lastrowid, None


def update_station(
        station_id: int,
        name: str,
        stream_url: str,
        homepage_url: Optional[str] = None,
        image: Optional[bytes] = None
    ) -> str | None:
    """Updates a station. Returns an error if the new stream URL collides with another station."""

    stream_url = normalize_url(stream_url, probe_https=True)
    if homepage_url:
        homepage_url = normalize_url(homepage_url)

    with database() as db:
        existing = db.execute(
            """
            SELECT name
            FROM internet_radio_stations
            WHERE id != ? AND (stream_url = ? OR stream_url = ?)
            """, (station_id, stream_url, https_variant(stream_url))
        ).fetchone()

        if existing:
            return f"A station with this stream URL already exists ('{existing['name']}')."

        db.execute(
            """
            UPDATE internet_radio_stations
            SET name=?, stream_url=?, homepage_url=?, image=?, image_mtime=?
            WHERE id=?
            """, (name, stream_url, homepage_url, image, time.time() if image else None, station_id)
        )

    return None


def list_radios() -> List[dict]:

    with database() as db:
        rows = db.execute(
            """
            SELECT id, name, stream_url, homepage_url, (image IS NOT NULL) AS has_image
            FROM internet_radio_stations
            ORDER BY name COLLATE NOCASE
            """
        ).fetchall()

    return [dict(r) for r in rows]


def delete_station(station_id: int) -> None:
    with database() as db:
        db.execute(
            """
            DELETE FROM internet_radio_stations 
            WHERE id=?
            """, (station_id,)
        )