from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from enum import StrEnum
from typing import Literal, Optional, List, Tuple

from beetsplug.beetstreamnext.constants import MAX_APP_PASSWORDS
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.core.storage.encryption import get_cipher
from beetsplug.beetstreamnext.utils.concurrency import RollingLimiter
from beetsplug.beetstreamnext.utils.text import sha256_hex


class AppPasswordRole(StrEnum):
    FULL = 'full'
    READONLY = 'readonly'   # blocks write endpoints


##

lastused_limiter = RollingLimiter(1, 60)

def _touch_table(db, row_id: int, ip: Optional[str]) -> None:

    if lastused_limiter.allow(row_id):
        db.execute(
            """
            UPDATE app_passwords 
            SET last_used = ?, last_ip = ? 
            WHERE id = ?
            """, (time.time(), ip, row_id)
        )


def create_app_password(
        username: str,
        name: str,
        role: 'AppPasswordRole' = AppPasswordRole.FULL,
        expires_at: Optional[float] = None,
        as_password: bool = False
) -> str:
    """
    Create and return a new secret. It is only available now.

    Without `as_password` only a hash of the secret is stored:
        it works as an API key but cannot be used for legacy (t+s / p) logins.
    """

    name = (name or '').strip()
    if not name:
        raise ValueError('An app password needs a name.')

    with database() as db:

        count = db.execute(
            """
            SELECT COUNT(*) 
            FROM app_passwords 
            WHERE username = ?
            """, (username,)
        ).fetchone()[0]

        if db.execute(
            """
            SELECT 1 
            FROM app_passwords 
            WHERE username = ? AND name = ? COLLATE NOCASE
            """, (username, name)
        ).fetchone():
            raise ValueError(f"You already have an app password named '{name}'.")

        if count >= MAX_APP_PASSWORDS:
            raise ValueError(f'Maximum of {MAX_APP_PASSWORDS} app passwords reached.')

        secret = secrets.token_urlsafe(32)
        encrypted = get_cipher().encrypt(secret.encode('utf-8')) if as_password else None

        db.execute(
            """
            INSERT INTO app_passwords (username, name, secret_encrypted, api_key_hash, role, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (username, name, encrypted, sha256_hex(secret), AppPasswordRole(role).value, expires_at)
        )

    return secret


def list_app_passwords(username: str) -> List[dict]:
    """Get the metadata of all existing app passwords."""

    with database() as db:
        rows = db.execute(
            """
            SELECT id, name, role, created, last_used, last_ip, expires_at, secret_encrypted IS NOT NULL AS as_password
            FROM app_passwords
            WHERE username = ?
            ORDER BY created, id
            """, (username,)
        ).fetchall()

    app_pwds = [dict(row) for row in rows]

    return app_pwds


def revoke_app_password(username: str, app_password_id: int | Literal['all']) -> int:

    with database() as db:
        cur = db.execute(
            """
            DELETE FROM app_passwords 
            WHERE username = ? AND (? = 'all' OR id = ?)
            """, (username, app_password_id, app_password_id)
        )

    return cur.rowcount


def authenticate_api_key(api_key: str, ip: Optional[str] = None) -> Tuple[str, 'AppPasswordRole'] | None:

    with database() as db:
        row = db.execute(
            """
            SELECT id, username, role, expires_at
            FROM app_passwords
            WHERE api_key_hash = ?
            """, (sha256_hex(api_key),)
        ).fetchone()

        if not row or (row['expires_at'] is not None and row['expires_at'] < time.time()):
            return None

        _touch_table(db, row['id'], ip)

    username = row['username']
    role = AppPasswordRole(row['role'])

    return username, role


def authenticate_legacy(
        username: str,
        token: str = '',
        salt: str = '',
        clearpass: str = '',
        ip: Optional[str] = None,
) -> AppPasswordRole | None:
    """Match legacy auth (t+s or p) against a user's app passwords."""

    with database() as db:
        rows = db.execute(
            """
            SELECT id, secret_encrypted, role, expires_at
            FROM app_passwords
            WHERE username = ? AND secret_encrypted IS NOT NULL
            """, (username,)
        ).fetchall()

        now = time.time()
        match = None

        for row in rows:

            secret = get_cipher().decrypt(row['secret_encrypted']).decode('utf-8')

            ok = False
            if token and salt:
                expected = hashlib.md5(f'{secret}{salt}'.encode('utf-8')).hexdigest()       # nosec B324
                ok = hmac.compare_digest(token.encode('utf-8'), expected.encode('utf-8'))

            elif clearpass:
                candidate = clearpass

                if candidate.startswith('enc:'):
                    try:
                        candidate = bytes.fromhex(candidate.removeprefix('enc:')).decode('utf-8')
                    except ValueError:
                        candidate = clearpass

                ok = hmac.compare_digest(candidate.encode('utf-8'), secret.encode('utf-8'))

            if ok and (row['expires_at'] is None or row['expires_at'] >= now) and match is None:
                match = row

        if match is not None:
            _touch_table(db, match['id'], ip)
            role = AppPasswordRole(match['role'])
            return role

    return None
