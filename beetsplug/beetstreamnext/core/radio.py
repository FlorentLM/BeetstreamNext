import time
from typing import Optional

from beetsplug.beetstreamnext.core.database import database


def create_station(
        name: str,
        stream_url: str,
        homepage_url: Optional[str] = None,
        image: Optional[bytes] = None
    ) -> None:

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