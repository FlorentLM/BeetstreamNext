from __future__ import annotations

import binascii
import json
import secrets
import os
import base64
import hashlib
import time
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from pathlib import Path
from functools import lru_cache

from beetsplug.beetstreamnext.core.runtime.console import print_box
from beetsplug.beetstreamnext.utils.ansi import TermColors
from beetsplug.beetstreamnext.constants import SESSION_KEY_ROTATION_DAYS
from beetsplug.beetstreamnext.core.storage.connection import database
from beetsplug.beetstreamnext.utils.general import api_bool
from beetsplug.beetstreamnext.utils.system import get_env


def _write_secret_file(path: Path, content: str) -> None:
    """Write a secret to disk, 0o600 from creation."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(content)
    except Exception:
        os.close(fd)   # only for if fdopen failed
        raise

# Secrets management


def rotate_session_key(cache_dir: str | Path) -> str:
    """
    Loads the admin session signing key from the cache directory, rotating it
    if it is older than _SESSION_KEY_ROTATION_DAYS.
    """

    cache_dir = Path(cache_dir)
    key_file = cache_dir / '.beetstreamnext_session'

    if key_file.exists():
        try:
            key_file.chmod(0o600)
            data = json.loads(key_file.read_text())
            age_days = (time.time() - data['generated_at']) / 86400
            if age_days < SESSION_KEY_ROTATION_DAYS:
                return data['key']
        except (json.JSONDecodeError, KeyError, OSError):
            pass   # malformed file, regenerate

    new_key = secrets.token_urlsafe(32)
    cache_dir.mkdir(parents=True, exist_ok=True)
    _write_secret_file(key_file, json.dumps({'key': new_key, 'generated_at': time.time()}))
    return new_key


def ensure_secret(db_path: str | Path) -> None:
    """
    Resolves BEETSTREAMNEXT_KEY. Called once at startup, before initialise_db().
    """

    db_path = Path(db_path)
    env_path = db_path.parent / '.env'

    externally_provided = bool(os.environ.get('BEETSTREAMNEXT_KEY'))

    # Load whatever is already in the env before deciding
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)
    else:
        load_dotenv(override=False)

    is_first_run = not db_path.exists()

    if is_first_run:
        if externally_provided:
            print_box([
                '',
                f'{TermColors.WARNING + TermColors.BOLD + TermColors.REVERSE}  BEETSTREAMNEXT: First run setup  {TermColors.ENDC}',
                '',
                'Using the BEETSTREAMNEXT_KEY already set in the environment.',
                '',
                "  ▶  It has NOT been written to disk here.",
                "  ▶  Keep it safe wherever you're managing it. If you lose it, stored",
                '     passwords become unrecoverable.',
                '',
            ], color=TermColors.WARNING)

        else:
            existing_lines = env_path.read_text().splitlines() if env_path.exists() else []
            already_set = {line.split('=', 1)[0] for line in existing_lines if '=' in line}

            if 'BEETSTREAMNEXT_KEY' in already_set:
                pass   # already resolved into os.environ by load_dotenv, nothing to do

            elif api_bool(get_env('BSN_NO_KEY_FILE')):
                enc_key = Fernet.generate_key().decode()
                os.environ['BEETSTREAMNEXT_KEY'] = enc_key

                print_box([
                    '',
                    f'{TermColors.WARNING + TermColors.BOLD + TermColors.REVERSE}  BEETSTREAMNEXT: First run setup  {TermColors.ENDC}',
                    '',
                    'An encryption key has been generated for your database:',
                    '',
                    f'{TermColors.BOLD}BEETSTREAMNEXT_KEY:',
                    f'{enc_key}{TermColors.ENDC}',
                    '',
                    f'{TermColors.BOLD}BSN_NO_KEY_FILE{TermColors.ENDC} is set: key was NOT written to disk.',
                    '',
                    "  ▶  Store it in your secrets manager. It won't be shown again.",
                    '  ▶  If you lose it, stored passwords will be unrecoverable.',
                    '',
                ], color=TermColors.WARNING)

            else:
                enc_key = Fernet.generate_key().decode()
                new_lines = existing_lines + [f'BEETSTREAMNEXT_KEY={enc_key}']
                os.environ['BEETSTREAMNEXT_KEY'] = enc_key

                _write_secret_file(env_path, '\n'.join(new_lines) + '\n')

                print_box([
                    '',
                    f'{TermColors.WARNING + TermColors.BOLD + TermColors.REVERSE}  BEETSTREAMNEXT: First run setup  {TermColors.ENDC}',
                    '',
                    'An encryption key has been generated for your database:',
                    '',
                    f'{TermColors.BOLD}BEETSTREAMNEXT_KEY:',
                    f'{enc_key}{TermColors.ENDC}',
                    '',
                    'It has been saved to:',
                    f'{env_path}',
                    '',
                    "  ▶  Make sure this file has the correct permissions.",
                    '  ▶  If you lose it, stored passwords will be unrecoverable.',
                    '',
                ], color=TermColors.WARNING)

    else:
        # Not first run, key must be present
        if not os.environ.get('BEETSTREAMNEXT_KEY'):
            print_box([
                '',
                f'{TermColors.FAIL + TermColors.BOLD + TermColors.REVERSE}  STARTUP FAILED: Missing required secret  {TermColors.ENDC}',
                '',
                f'Add the {TermColors.BOLD}BEETSTREAMNEXT_KEY{TermColors.ENDC} to:',
                f'{env_path}',
                '',
                'If you have lost the BEETSTREAMNEXT_KEY, stored passwords',
                'are unrecoverable. Delete the database and run setup again.',
                '',
            ], color=TermColors.FAIL)
            exit(1)


@lru_cache(maxsize=1)
def _cipher_for(key: str) -> Fernet | None:
    """Fernet for a given key string. Cached for the process lifetime."""
    try:
        return Fernet(key)
    except (ValueError, TypeError):
        return None


@lru_cache(maxsize=1)
def _hash_for(key: str) -> str:
    """SHA256 of the decoded key bytes. Also cached."""
    return hashlib.sha256(base64.urlsafe_b64decode(key)).hexdigest()


def get_cipher() -> Fernet | None:
    key = os.environ.get('BEETSTREAMNEXT_KEY')
    if not key:
        return None
    return _cipher_for(key)


def get_key_hash() -> str | None:
    key = os.environ.get('BEETSTREAMNEXT_KEY')
    if not key:
        return None
    try:
        return _hash_for(key)
    except binascii.Error:
        return None


def verify_key() -> bool:

    with database() as db:
        result = db.execute("""SELECT value FROM encryption WHERE key = 'key_hash'""").fetchone()

    stored_hash = result[0] if result else None
    current_hash = get_key_hash()

    return current_hash == stored_hash
