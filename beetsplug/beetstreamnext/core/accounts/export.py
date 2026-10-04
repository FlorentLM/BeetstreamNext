from __future__ import annotations

import time
from typing import Any, Dict, List

from beetsplug.beetstreamnext.core.accounts.users_crud import get_userdata
from beetsplug.beetstreamnext.core.storage.connection import database


def _get_rows(db, sql: str, params: tuple) -> List[Dict[str, Any]]:
    return [dict(row) for row in db.execute(sql, params).fetchall()]


def export_user_data(username: str) -> Dict[str, Any]:
    """
    Everything BeetstreamNext stores about a user as a serialisable dict.
    Credentials are not included and the avatar is reported as a flag only.
    """

    profile = get_userdata(username)
    if not profile:
        raise ValueError(f"User '{username}' does not exist.")

    has_avatar = bool(profile.pop('avatarLastChanged', None))
    profile.pop('api_key_hash', None)
    profile['hasAvatar'] = has_avatar

    with database() as db:
        shares = _get_rows(db, "SELECT id, description, expires, created, visit_count FROM shares WHERE username = ?", (username,))
        for share in shares:
            share['items'] = [r['item_id'] for r in db.execute(
                "SELECT item_id FROM share_entries WHERE share_id = ?", (share['id'],)
            ).fetchall()]

        play_queue = _get_rows(db, "SELECT current, position, changed, changed_by FROM play_queue WHERE username = ?", (username,))
        if play_queue:
            play_queue[0]['entries'] = [r['song_id'] for r in db.execute(
                "SELECT song_id FROM play_queue_entries WHERE username = ? ORDER BY position", (username,)
            ).fetchall()]

        return {
            'exported_at': time.time(),
            'profile': profile,
            'likes': _get_rows(db, "SELECT item_id, starred_at FROM likes WHERE username = ?", (username,)),
            'ratings': _get_rows(db, "SELECT item_id, rating, rated_at FROM ratings WHERE username = ?", (username,)),
            'bookmarks': _get_rows(db, "SELECT song_id, position, comment, created, changed FROM bookmarks WHERE username = ?", (username,)),
            'play_stats': _get_rows(db, "SELECT song_id, play_count, last_played FROM play_stats WHERE username = ?", (username,)),
            'play_queue': play_queue[0] if play_queue else None,
            'shares': shares,
            'podcast_subscriptions': _get_rows(db, """
                SELECT c.url, c.title, s.subscribed_at
                FROM podcast_subscriptions s JOIN podcast_channels c ON c.id = s.channel_id
                WHERE s.username = ?
            """, (username,)),
            'podcast_episode_downloads': _get_rows(db, """
                SELECT e.guid, e.title, c.url AS channel_url, d.requested_at
                FROM podcast_episode_downloads d
                JOIN podcast_episodes e ON e.id = d.episode_id
                JOIN podcast_channels c ON c.id = e.channel_id
                WHERE d.username = ?
            """, (username,)),
            'chat_messages': _get_rows(db, "SELECT time, message FROM chat_messages WHERE username = ?", (username,)),
        }
