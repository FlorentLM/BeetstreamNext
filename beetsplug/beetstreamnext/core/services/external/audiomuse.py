from __future__ import annotations

import requests

from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.config.store import settings_store


def audiomuse_get(path: str, params: dict, timeout: float = 10.0):
    """GET a AudioMuse-AI endpoint. Returns (data, error_message)."""

    audiomuse_url = settings_store.get('audiomuse_url')
    if not audiomuse_url:
        return None, 'AudioMuse-AI is not configured on this server.'

    headers = {}
    api_token = settings_store.get('audiomuse_api_token')
    if api_token:
        headers['Authorization'] = f'Bearer {api_token}'

    try:
        url = f"{audiomuse_url.rstrip('/')}{path}"
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
        if not response.ok:
            bsn_logger.error(f'AudioMuse-AI API error: {response.status_code} - {response.text}')
            return None, 'Failed to communicate with AudioMuse-AI.'
        return response.json(), None

    except requests.RequestException as e:
        bsn_logger.error(f'AudioMuse-AI connection failed: {e}')
        return None, 'Could not connect to AudioMuse-AI.'


def _audiomuse_post(path: str, json_body: dict, timeout: float = 10.0):
    """POST to a AudioMuse-AI endpoint. Returns (data, error_message)."""

    audiomuse_url = settings_store.get('audiomuse_url')
    if not audiomuse_url:
        return None, 'AudioMuse-AI is not configured on this server.'

    headers = {}
    api_token = settings_store.get('audiomuse_api_token')
    if api_token:
        headers['Authorization'] = f'Bearer {api_token}'

    try:
        url = f"{audiomuse_url.rstrip('/')}{path}"
        response = requests.post(url, json=json_body, headers=headers, timeout=timeout)
        if not response.ok:
            bsn_logger.error(f'AudioMuse-AI API error: {response.status_code} - {response.text}')
            return None, 'Failed to communicate with AudioMuse-AI.'
        return response.json(), None

    except requests.RequestException as e:
        bsn_logger.error(f'AudioMuse-AI connection failed: {e}')
        return None, 'Could not connect to AudioMuse-AI.'


def test_audiomuse_connection() -> tuple[bool, str]:
    """Ping AudioMuse-AI's health endpoint. Returns (ok, message)."""

    if not settings_store.get('audiomuse_url'):
        return False, 'AudioMuse-AI URL is not configured.'

    data, err = audiomuse_get('/api/health', {}, timeout=8.0)
    if err:
        return False, err
    return True, 'Connected to AudioMuse-AI successfully.'


def start_audiomuse_analysis() -> tuple[bool, str]:
    """Trigger a AudioMuse-AI analysis over the whole library."""

    data, err = _audiomuse_post('/api/analysis/start', {'num_recent_albums': 0}, timeout=15.0)
    if err:
        return False, err

    task_id = (data or {}).get('task_id')
    if task_id:
        return True, f'Fingerprinting started on AudioMuse-AI (task {task_id}).'
    return True, 'Fingerprinting started on AudioMuse-AI.'
