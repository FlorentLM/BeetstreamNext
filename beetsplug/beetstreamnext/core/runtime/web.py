from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Optional

import flask

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.utils.system import get_mimetype, safe_filename
from beetsplug.beetstreamnext.constants import LOOPBACK_IPS
from beetsplug.beetstreamnext.utils.net import parse_host
from beetsplug.beetstreamnext.core.config.store import settings_store


def read_upload(field: str, max_bytes: int) -> bytes | None:
    """Content of the uploaded file. ValueError if size > `max_bytes`."""
    file = flask.request.files.get(field)
    if file is None or not file.filename:
        return None

    data = file.read(max_bytes + 1)     # Hard cap
    if len(data) > max_bytes:
        raise ValueError(f'File too large (max {max_bytes // 1024} KB).')

    return data


_warned_host_header = False


def request_url(path_part: str) -> str:
    """
    Build an absolute URL for 'path_part' using the current request's scheme+host.
    """
    global _warned_host_header

    scheme = 'https' if (flask.request.is_secure or settings_store.get('reverse_proxy')) else 'http'
    host = parse_host(flask.request.host, strict=True)

    if not _warned_host_header and not app.config.get('trusted_hosts') and host.host not in LOOPBACK_IPS:
        _warned_host_header = True
        bsn_logger.warning(
            'Tokenised links are built from the request\'s Host header, but `trusted_hosts` '
            'is not set. This is dangerous, consider setting `trusted_hosts` (and `external_hostname` for share links).'
        )

    return f'{scheme}://{host.netloc}{path_part}'


def external_url(path_part: str) -> str:
    """Build an absolute URL for 'path_part' on the configured public share hostname."""
    external_host = settings_store.get('external_hostname')
    if not external_host:
        return request_url(path_part)

    reverse_proxy = settings_store.get('reverse_proxy')
    scheme = 'https' if (flask.request.is_secure or reverse_proxy) else 'http'

    if not reverse_proxy and parse_host(external_host).port is None:
        external_host = f'{external_host}:{settings_store.get("port")}'

    return f'{scheme}://{external_host}{path_part}'


def content_disposition(download_name: str) -> str:
    """'attachment' header with an ASCII fallback and the RFC 6266/5987 UTF-8 `filename*` form."""
    name = safe_filename(download_name)
    ascii_name = name.encode('ascii', 'replace').decode().replace('?', '_')
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(name, safe='')}"


def _sendfile_offload(file_path: Path, as_attachment: bool, download_name: Optional[str]) -> flask.Response | None:
    """
    Hand the file off to the reverse proxy (Nginx/Apache) instead of streaming it through
    Python (if configured). Returns None if offloading isn't enabled/possible.
    """
    if not settings_store.get('reverse_proxy'):
        return None

    method = settings_store.get('sendfile_method')
    if method == 'off':
        return None

    resp = flask.Response(status=200, mimetype=get_mimetype(file_path))
    if as_attachment:
        resp.headers['Content-Disposition'] = content_disposition(download_name or file_path.name)

    if method == 'x-sendfile':
        resp.headers['X-Sendfile'] = str(file_path)
        return resp

    if method == 'x-accel-redirect':
        root = Path(app.config['root_directory'])
        try:
            rel = file_path.resolve().relative_to(root.resolve())
        except ValueError:
            bsn_logger.warning(f"'{file_path}' is outside root_directory, can't use X-Accel-Redirect.")
            return None
        prefix = settings_store.get('sendfile_internal_prefix').rstrip('/')
        resp.headers['X-Accel-Redirect'] = f'{prefix}/{rel.as_posix()}'
        return resp

    return None


def send_file(
        file_path: str | Path,
        as_attachment: bool = False,
        download_name: Optional[str] = None
    ) -> flask.Response | None:

    file_path = Path(file_path)
    if download_name is not None:
        download_name = safe_filename(download_name, fallback=file_path.name)

    offloaded = _sendfile_offload(file_path, as_attachment, download_name)
    if offloaded is not None:
        return offloaded

    try:
        return flask.send_file(
            file_path,
            mimetype=get_mimetype(file_path),
            as_attachment=as_attachment,
            download_name=download_name
        )
    except OSError as e:
        bsn_logger.error(f"Failed to serve file '{file_path}': {e}")
        return None
