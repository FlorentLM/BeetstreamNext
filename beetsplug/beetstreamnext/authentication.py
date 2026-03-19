import hashlib
import hmac
import sqlite3
import flask
from urllib.parse import unquote

from beetsplug.beetstreamnext import app
from beetsplug.beetstreamnext.db import load_userdata


def get_user(api_key_hash: str) -> str:
    db_path = flask.current_app.config.get('DB_PATH')
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT username FROM users WHERE api_key_hash = ?", (api_key_hash,)).fetchone()
    conn.close()
    return row[0] if row else None


def authenticate(req_values):
    api_key = unquote(req_values.get('apiKey', ''))
    user = unquote(req_values.get('u', ''))
    token = unquote(req_values.get('t', ''))
    salt = unquote(req_values.get('s', ''))
    clearpass = unquote(req_values.get('p', ''))

    legacy_auth_enabled = app.config.get('legacy_auth', True)

    # API Key (modern)
    if api_key:
        if user or token or salt or clearpass:
            return False, 43, None

        api_key_hash = hashlib.sha256(api_key.encode('utf-8')).hexdigest()
        found_user = get_user(api_key_hash)
        if found_user:
            return True, 0, found_user
        return False, 40, None

    if not legacy_auth_enabled:
        return False, 42, None

    # Legacy (MD5 / password)
    if not user:
        return False, 10, None

    user_data = load_userdata(user, fields=['password'])
    if not user_data:
        # Dummy query + dummy comparison to keep response time constant regardless if username exists or not
        load_userdata('', fields=['password'])
        hmac.compare_digest('dummy', 'comparison')
        return False, 40, None

    stored_password = user_data['password']

    if token and salt:
        expected = hashlib.md5(f"{stored_password}{salt}".encode('utf-8')).hexdigest().lower()
        if hmac.compare_digest(token, expected):
            return True, 0, user
    elif clearpass:
        if clearpass.startswith('enc:'):
            try:
                decoded = bytes.fromhex(clearpass.removeprefix('enc:')).decode('utf-8')
            except ValueError:
                return False, 40, None
            ok = hmac.compare_digest(decoded, stored_password)
        else:
            ok = hmac.compare_digest(clearpass, stored_password)
        if ok:
            return True, 0, user

    return False, 40, None