import time
from typing import Optional

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.database import database
from beetsplug.beetstreamnext.core.external import query_radio_browser, capped_image_fetch, fetch_favicon, normalize_url


def create_station(
        name: str,
        stream_url: str,
        homepage_url: Optional[str] = None,
        image: Optional[bytes] = None
    ) -> None:

    stream_url = normalize_url(stream_url, probe_https=True)
    if homepage_url:
        homepage_url = normalize_url(homepage_url)

    if not image and app.config.get('fetch_radio_images'):

        resp = query_radio_browser(name, limit=1)
        if resp and resp[0].get('favicon'):
            image = capped_image_fetch(resp[0]['favicon'])

        if not image and homepage_url:
            image = fetch_favicon(homepage_url)

    with database() as db:
        db.execute(
            """
            INSERT INTO internet_radio_stations (name, stream_url, homepage_url, image, image_mtime) 
            VALUES (?, ?, ?, ?, ?)
            """, (name, stream_url, homepage_url, image, time.time() if image else None)
        )


def update_station(
        station_id: int,
        name: str,
        stream_url: str,
        homepage_url: Optional[str] = None,
        image: Optional[bytes] = None
    ) -> None:

    stream_url = normalize_url(stream_url, probe_https=True)
    if homepage_url:
        homepage_url = normalize_url(homepage_url)

    with database() as db:
        db.execute(
            """
            UPDATE internet_radio_stations 
            SET name=?, stream_url=?, homepage_url=?, image=?, image_mtime=? 
            WHERE id=?
            """, (name, stream_url, homepage_url, image, time.time() if image else None, station_id)
        )


def delete_station(station_id: int) -> None:
    with database() as db:
        db.execute(
            """
            DELETE FROM internet_radio_stations 
            WHERE id=?
            """, (station_id,)
        )