import json
import threading
from typing import Any, Dict, Optional, Callable

from beetsplug.beetstreamnext.utils.general import api_bool
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.constants import bsn_logger
from beetsplug.beetstreamnext.core.database import database, get_cipher
from beetsplug.beetstreamnext.core.security import ip_filter, rate_limiter
from beetsplug.beetstreamnext.schemas import SETTINGS_SCHEMA


def coerce_setting(value: Any, type_str: str) -> Any:
    """Cast a raw value to the type defined in the settings schema."""
    if type_str == 'bool':
        return api_bool(value)
    if type_str == 'int':
        return int(value)
    if type_str == 'str':
        return '' if value is None else str(value)
    if type_str == 'list[str]':
        if isinstance(value, str):
            return [s.strip() for s in value.split(',') if s.strip()]
        return [str(s) for s in (value or [])]
    raise ValueError(f'Unknown type: {type_str}')


class SettingsStore:
    def __init__(self):
        self._lock = threading.RLock()

        self._cache: Dict[str, Any] = {}
        self._directly_applicable: Optional[Dict[str, Callable]] = None

    def _init_directly_applicable(self):
        """Bind settings that don't neet a restart."""

        if self._directly_applicable is not None:
            return

        self._directly_applicable = {}

        # Simple app.config updates
        for k in ('trusted_hosts', 'legacy_auth', 'never_transcode',
                  'fetch_artists_images', 'save_artists_images',
                  'save_album_art',
                  'fetch_lyrics', 'save_lyrics',
                  'fetch_album_version', 'save_album_version',
                  'lastfm_api_key',
                  'replaygain_enabled', 'replaygain_preamp',
                  'replaygain_fallback', 'audio_peak_limit'):
            self._directly_applicable[k] = lambda v, key=k: app.config.update({key: v})

        # Security object updates
        self._directly_applicable.update({
            'ip_whitelist': lambda v: setattr(ip_filter, 'whitelist', v),
            'ip_blacklist': lambda v: setattr(ip_filter, 'blacklist', v),
            'rate_limit_max_failures': lambda v: setattr(rate_limiter, 'max_failures', v),
            'rate_limit_block_window': lambda v: setattr(rate_limiter, 'block_window', v),
        })

    def initialise(self, yaml_defaults: Optional[Dict[str, Any]] = None):
        """
        Populate the in-memory cache and apply settings to live runtime stuff.
        Resolution order (per key): db -> yaml default -> schema default.
        """
        self._init_directly_applicable()
        yaml_defaults = yaml_defaults or {}

        # Load from db
        db_values = {}
        cipher = get_cipher()

        with database() as db:
            rows = db.execute(
                """
                SELECT key, value, encrypted 
                FROM settings
                """
            ).fetchall()

        for row in rows:
            key, raw, is_enc = row['key'], row['value'], row['encrypted']
            if key not in SETTINGS_SCHEMA:
                continue

            try:
                if is_enc and cipher:
                    db_values[key] = json.loads(cipher.decrypt(raw.encode()).decode())
                else:
                    db_values[key] = json.loads(raw) if raw is not None else None
            except Exception:
                bsn_logger.warning(f"Failed to load setting '{key}' from db, using fallback.")

        with self._lock:
            self._cache.clear()

            for key, spec in SETTINGS_SCHEMA.items():
                val = db_values.get(key)
                if val is None:
                    val = yaml_defaults.get(key, spec['default'])

                try:
                    val = coerce_setting(val, spec['type'])
                    if 'validator' in spec:
                        val = spec['validator'](val)
                except (ValueError, TypeError) as e:
                    bsn_logger.warning(f"Invalid value for '{key}' ({e}), using default.")
                    val = spec['default']

                self._cache[key] = val

                # Apply what does not need a restart
                if key in self._directly_applicable:
                    try:
                        self._directly_applicable[key](val)
                    except Exception as e:
                        bsn_logger.error(f"Failed to apply setting '{key}': {e}")

    def get(self, key: str) -> Any:
        if key not in SETTINGS_SCHEMA:
            raise KeyError(f'Unknown setting: {key}')
        with self._lock:
            return self._cache.get(key, SETTINGS_SCHEMA[key]['default'])

    def set(self, key: str, value: Any) -> Any:
        """Validate, persist, apply live, cache. Returns the coerced/validated value."""

        if key not in SETTINGS_SCHEMA:
            raise KeyError(f'Unknown setting: {key}')

        spec = SETTINGS_SCHEMA[key]
        value = coerce_setting(value, spec['type'])
        if 'validator' in spec:
            value = spec['validator'](value)

        serializable_value = list(value) if isinstance(value, set) else value

        # Persist to db
        cipher = get_cipher()
        is_sensitive = spec.get('sensitive', False)

        if is_sensitive and cipher:
            stored_val = cipher.encrypt(json.dumps(serializable_value).encode()).decode()
            encrypted_flag = 1
        else:
            if is_sensitive:
                bsn_logger.warning(f"Storing sensitive setting '{key}' unencrypted (no key).")
            stored_val = json.dumps(serializable_value)
            encrypted_flag = 0

        with database() as db:
            db.execute(
                """
                INSERT INTO settings (key, value, encrypted, updated_at)
                VALUES (?, ?, ?, unixepoch())
                ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                                               encrypted=excluded.encrypted,
                                               updated_at=excluded.updated_at
                """, (key, stored_val, encrypted_flag)
            )

        # Update cache
        with self._lock:
            self._cache[key] = value

        # Apply live ones
        if key in self._directly_applicable:
            try:
                self._directly_applicable[key](value)
            except Exception as e:
                bsn_logger.error(f"Persisted '{key}' but failed to apply live: {e}")
                raise

        return value

    def get_for_ui(self, category: str) -> Dict[str, Dict[str, Any]]:
        """For UI rendering. Sensitive values are only reported as 'is_set' booleans."""
        result = {}
        with self._lock:
            for key, spec in SETTINGS_SCHEMA.items():
                if spec.get('category') != category:
                    continue

                val = self._cache.get(key, spec['default'])
                entry = {
                    'type': spec['type'],
                    'description': spec.get('description', ''),
                    'requires_restart': bool(spec.get('requires_restart')),
                    'sensitive': bool(spec.get('sensitive')),
                }
                if entry['sensitive']:
                    entry['is_set'] = bool(val)
                else:
                    entry['value'] = val
                result[key] = entry
        return result


settings_store = SettingsStore()