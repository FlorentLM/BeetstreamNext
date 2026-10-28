from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Optional


# scrypt parameters for new hashes
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32
MAXMEM = 128 * 1024 * 1024
PREFIX = 'scrypt'

_b64 = lambda raw: base64.b64encode(raw).decode('ascii')       # noqa: E731


def hash_password(password: str) -> str:
    """One-way hash of a password. Self-describing string ('scrypt$N$r$p$salt$hash')."""

    salt = secrets.token_bytes(SALT_BYTES)

    key = hashlib.scrypt(
        password=password.encode('utf-8'),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_BYTES,
        maxmem=MAXMEM
    )

    return f'{PREFIX}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(key)}'


DUMMY_HASH = hash_password(secrets.token_urlsafe(24))

def verify_password(password: str, stored: Optional[str]) -> bool:

    if stored is None:
        stored = DUMMY_HASH

    try:
        prefix, n, r, p, salt_b64, key_b64 = stored.split('$')
        if prefix != PREFIX:
            return False

        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(key_b64)

        derived = hashlib.scrypt(
            password=password.encode('utf-8'),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=KEY_BYTES,
            maxmem=MAXMEM
        )

    except (ValueError, TypeError):
        return False

    return hmac.compare_digest(derived, expected)


MIN_PASSWORD_LEN = 8

PASSWORD_RULES = (
    ('length', f'At least {MIN_PASSWORD_LEN} characters', lambda p: len(p) >= MIN_PASSWORD_LEN),
    ('lower', 'A lowercase letter', lambda p: any(c.islower() for c in p)),
    ('upper', 'An uppercase letter', lambda p: any(c.isupper() for c in p)),
    ('digit', 'A digit', lambda p: any(c.isdigit() for c in p)),
    ('special', 'A special character', lambda p: any(not c.isalnum() and not c.isspace() for c in p)),
) # Keys and descriptions are also sent to the client-side strength meter


def password_problems(password: str) -> list[str]:
    """Descriptions of the rules the password fails."""
    return [desc for _key, desc, ok in PASSWORD_RULES if not ok(password)]


def validate_password(password: str) -> str:
    """Return the password unchanged, or raise ValueError listing the missing requirements."""
    problems = password_problems(password)
    if problems:
        raise ValueError('Password is too weak. It needs: ' + '; '.join(p.lower() for p in problems) + '.')
    return password
