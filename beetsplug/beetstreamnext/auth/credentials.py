from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Optional, Tuple

from beetsplug.beetstreamnext.core.database import get_cipher
from beetsplug.beetstreamnext.core.users_crud import get_userdata


# Dummy strings comparison when username not found
_DUMMY_PASSWORD = secrets.token_urlsafe(24)
_DUMMY_TOKEN: Optional[bytes] = None   # set lazily (cipher may not exist yet)


def _dummy_stored_password() -> str:
    """Decrypt a dummy Fernet token to mimic the cost of real password retrieval."""

    global _DUMMY_TOKEN

    cipher = get_cipher()
    if cipher is None:
        return _DUMMY_PASSWORD

    if _DUMMY_TOKEN is None:
        _DUMMY_TOKEN = cipher.encrypt(_DUMMY_PASSWORD.encode('utf-8'))

    try:
        return cipher.decrypt(_DUMMY_TOKEN).decode('utf-8')
    except Exception:
        return _DUMMY_PASSWORD


def check_password(
        username: str,
        token: Optional[str] = None,
        salt: Optional[str] = None,
        clearpass: Optional[str] = None
    ) -> Tuple[bool, int, str | None]:

    stored_password = get_userdata(username, fields=['password'], include_password=True).get('password')

    if not stored_password:
        stored_password = _dummy_stored_password()
        user_found = False
    else:
        user_found = True

    stored_password_b = stored_password.encode('utf-8')

    ok = False
    if token and salt:
        expected = hashlib.md5(f"{stored_password}{salt}".encode('utf-8')).hexdigest().lower()
        ok = hmac.compare_digest(token.encode('utf-8'), expected.encode('utf-8'))

    elif clearpass:
        if clearpass.startswith('enc:'):
            try:
                decoded = bytes.fromhex(clearpass.removeprefix('enc:')).decode('utf-8')
                ok = hmac.compare_digest(decoded.encode('utf-8'), stored_password_b)
            except ValueError:
                ok = hmac.compare_digest(clearpass.encode('utf-8'), stored_password_b)
        else:
            ok = hmac.compare_digest(clearpass.encode('utf-8'), stored_password_b)

    if ok and user_found:
        return True, 0, username

    # 40: "Wrong username or password."
    return False, 40, None


def webui_login(username: str, password: str) -> Tuple[bool, Optional[str]]:
    """
    Password check for the WebUI's login form.
    """
    success, _, matched_username = check_password(username, clearpass=password)
    return success, matched_username
