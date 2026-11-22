from __future__ import annotations

from typing import Any
import requests

from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.services.external.session import http_session, service_session


class ExternalService:
    """
    A third party web service BeetstreamNext talks to.
    """

    label: str = ''             # Service name for logs and messages
    timeout: float = 8          # sec
    self_hosted: bool = False   # Allow reach hosts whitelisted in 'local_services'

    def request(self, method: str, url: str, *, cached: bool = False, **kwargs) -> requests.Response | None:

        kwargs.setdefault('timeout', self.timeout)

        if not cached:
            kwargs.update(expire_after=0, force_refresh=True)

        session = service_session() if self.self_hosted else http_session()

        try:
            response = session.request(method, url, **kwargs)

        except requests.exceptions.RequestException as e:
            bsn_logger.warning(f'{self.label or url} request failed: {type(e).__name__}')
            if 'non-public address' in str(e):
                bsn_logger.warning(
                    f'{self.label or url} is on a private address: add its hostname or IP to the '
                    "'local_services' setting to allow it."
                )
            return None

        if getattr(response, 'from_cache', False):
            bsn_logger.debug(f'Served from cache: {self.label or url}')

        return response

    def get_json(self, url: str, *, cached: bool = True, **kwargs) -> dict[str, Any]:
        """GET a JSON object (cached by default)."""

        response = self.request('GET', url, cached=cached, **kwargs)
        if response is None or not response.ok:
            return {}
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
