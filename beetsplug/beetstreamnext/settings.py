import json
import threading
from typing import Any, Dict, Optional, Callable

from beetsplug.beetstreamnext.utils.text import split_list
from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.logging import bsn_logger
from beetsplug.beetstreamnext.core.database import database, get_cipher
from beetsplug.beetstreamnext.core.security import ip_filter, rate_limiter
from beetsplug.beetstreamnext.schemas import SETTINGS_SCHEMA


def coerce_setting(value: Any, type_str: str) -> Any:
    """Cast a raw value to the type defined in the settings schema."""
    if type_str == 'bool':
        from beetsplug.beetstreamnext.utils.general import api_bool     # TODO: should avoid local imports, need to reorganise stuff again :(
        return api_bool(value)
    if type_str == 'int':
        return int(value)
    if type_str == 'str':
        return '' if value is None else str(value)
    if type_str == 'list[str]':
        return split_list(value)
    raise ValueError(f'Unknown type: {type_str}')


def _merge_pinned(pinned: Any, extra: Any) -> Any:
    if isinstance(pinned, set):
        return pinned | set(extra)
    merged = list(pinned)
    merged += [v for v in extra if v not in merged]
    return merged


def _strip_pinned(value: Any, pinned: Any) -> Any:
    if isinstance(pinned, set):
        return set(value) - pinned
    pinned_list = list(pinned)
    return [v for v in value if v not in pinned_list]


class SettingsStore:
    def __init__(self):
        self._lock = threading.RLock()

        self._cache: Dict[str, Any] = {}
        self._locked: set = set()       # settings explicitly set via a CLI flag/env var/YAML/beets-config are locked
        self._pinned: Dict[str, Any] = {}   # list[str] keys: explicitly set items are locked
        self._directly_applicable: Optional[Dict[str, Callable]] = None

    def _init_directly_applicable(self):
        """Bind settings that don't neet a restart."""

        if self._directly_applicable is not None:
            return

        self._directly_applicable = {}

        # Simple app.config updates
        for k in ('trusted_hosts', 'legacy_auth',
                  'never_transcode', 'lastfm_api_key',
                  'fetch_artists_images', 'save_artists_images',
                  'fetch_artists_biographies', 'save_album_art', 'follow_playlist_embedded_urls',
                  'fetch_lyrics', 'save_lyrics',
                  'fetch_album_version', 'save_album_version', 'discogs_ratings', 'ignored_articles',
                  'fetch_radio_images',
                  'enable_radio_discovery',
                  'enable_podcast_discovery',
                  'replaygain_enabled', 'replaygain_preamp',
                  'replaygain_fallback', 'audio_peak_limit'):
            self._directly_applicable[k] = lambda v, key=k: app.config.update({key: v})

        # Security object updates
        self._directly_applicable.update({
            'ip_whitelist': lambda v: setattr(ip_filter, 'whitelist', v),
            'ip_blacklist': lambda v: setattr(ip_filter, 'blacklist', v),
            'rate_limit_max_failures': lambda v: setattr(rate_limiter, 'max_failures', v),
            'rate_limit_block_window': lambda v: setattr(rate_limiter, 'block_window', v),
            'rate_limit_ip_max_failures': lambda v: setattr(rate_limiter, 'ip_max_failures', v),
            'rate_limit_ip_block_window': lambda v: setattr(rate_limiter, 'ip_block_window', v),
        })

    def initialise(self, yaml_defaults: Optional[Dict[str, Any]] = None):
        """
        Populate the in-memory cache and apply settings to live runtime stuff.
        Resolution order (per key): CLI flag/env var/YAML/beets-config (yaml_defaults) -> db -> schema default.
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
            self._locked = set()
            self._pinned = {}

            for key, spec in SETTINGS_SCHEMA.items():
                is_list = spec['type'] == 'list[str]'
                external = key in yaml_defaults

                if external and is_list:
                    val = list(yaml_defaults[key] or []) + list(db_values.get(key) or [])
                elif external:
                    self._locked.add(key)
                    val = yaml_defaults[key]
                else:
                    val = db_values.get(key)
                    if val is None:
                        val = spec['default']

                try:
                    val = coerce_setting(val, spec['type'])
                    if 'validator' in spec:
                        val = spec['validator'](val)
                except (ValueError, TypeError) as e:
                    bsn_logger.warning(f"Invalid value for '{key}' ({e}), using default.")
                    val = spec['default']

                if external and is_list:
                    try:
                        pinned_val = coerce_setting(yaml_defaults[key], spec['type'])
                        if 'validator' in spec:
                            pinned_val = spec['validator'](pinned_val)
                    except (ValueError, TypeError) as e:
                        bsn_logger.warning(f"Invalid pinned value for '{key}' ({e}), ignoring it.")
                        pinned_val = spec['default']
                    self._pinned[key] = pinned_val

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

    def locked(self, key: str) -> bool:
        """
        True if setting is explicitly set by a CLI flag/env var/YAML/beets-config.
        """
        with self._lock:
            return key in self._locked
    
    def pinned(self, key: str) -> Any:
        """
        For a list setting: items explicitly set from a CLI flag/env var/YAML/beets-config are locked.
        Others can still be added/removed.
        """
        with self._lock:
            return self._pinned.get(key, SETTINGS_SCHEMA[key]['default'] if key in SETTINGS_SCHEMA else [])

    def would_change(self, key: str, value: Any) -> bool:
        """
        True if 'value' (after coercing/validating) differs from its currently set value.
        """
        if key not in SETTINGS_SCHEMA:
            raise KeyError(f'Unknown setting: {key}')

        spec = SETTINGS_SCHEMA[key]
        try:
            coerced = coerce_setting(value, spec['type'])
            if 'validator' in spec:
                coerced = spec['validator'](coerced)
        except (ValueError, TypeError):
            return True  # let set() raise the real error

        return coerced != self.get(key)

    def set(self, key: str, value: Any) -> Any:
        """Validate, persist, apply live, cache. Returns the coerced/validated value."""

        if key not in SETTINGS_SCHEMA:
            raise KeyError(f'Unknown setting: {key}')

        pinned = self._pinned.get(key)

        if pinned is None and self.locked(key):
            raise PermissionError(
                f"'{key}' is explicitly set via a CLI flag, environment variable, or config file. It can't be modified from here."
            )

        spec = SETTINGS_SCHEMA[key]
        value = coerce_setting(value, spec['type'])
        if 'validator' in spec:
            value = spec['validator'](value)


        # For list setting the pinned items always come back
        to_store = _strip_pinned(value, pinned) if pinned is not None else value
        serializable_value = list(to_store) if isinstance(to_store, set) else to_store

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

        final_value = _merge_pinned(pinned, to_store) if pinned is not None else value

        # Update cache
        with self._lock:
            self._cache[key] = final_value

        # Apply live ones
        if key in self._directly_applicable:
            try:
                self._directly_applicable[key](final_value)
            except Exception as e:
                bsn_logger.error(f"Persisted '{key}' but failed to apply live: {e}")
                raise

        return final_value

    def reset(self, key: str) -> Any:

        if key not in SETTINGS_SCHEMA:
            raise KeyError(f'Unknown setting: {key}')

        pinned = self._pinned.get(key)

        if pinned is None and self.locked(key):
            raise PermissionError(
                f"'{key}' is set explicitly via a CLI flag, environment variable, or config file. It can't be reset from here."
            )

        with database() as db:
            db.execute(
                """
                DELETE FROM settings 
                WHERE key = ?
                """, (key,)
            )

        spec = SETTINGS_SCHEMA[key]
        value = pinned if pinned is not None else spec['default']

        with self._lock:
            self._cache[key] = value

        if key in self._directly_applicable:
            try:
                self._directly_applicable[key](value)
            except Exception as e:
                bsn_logger.error(f"Reset '{key}' but failed to apply live: {e}")
                raise

        return value

    def get_for_ui(self, category: str) -> Dict[str, Dict[str, Any]]:
        """For UI rendering. Sensitive values are only reported as 'is_set' booleans."""
        result = {}
        standalone = app.config.get('STANDALONE_MODE', False)

        with self._lock:
            for key, spec in SETTINGS_SCHEMA.items():

                if spec.get('category') != category:
                    continue

                if spec.get('standalone_only') and not standalone:
                    continue

                val = self._cache.get(key, spec['default'])
                pinned = self._pinned.get(key)

                overridden = bool(_strip_pinned(val, pinned)) if pinned is not None else val != spec['default']

                entry = {
                    'type': spec['type'],
                    'description': spec.get('description', ''),
                    'requires_restart': bool(spec.get('requires_restart')),
                    'sensitive': bool(spec.get('sensitive')),
                    'locked': key in self._locked,
                    'overridden': overridden,
                    'pinned': list(pinned) if pinned is not None else [],
                }
                if 'choices' in spec:
                    entry['choices'] = spec['choices']
                if 'help' in spec:
                    entry['help'] = spec['help']
                if entry['sensitive']:
                    entry['is_set'] = bool(val)
                else:
                    entry['value'] = val
                    if spec['type'] == 'str' and not val:
                        entry['placeholder'] = spec['on_empty']() if 'on_empty' in spec else 'Not set'
                result[key] = entry
        return result


settings_store = SettingsStore()