from __future__ import annotations

import requests

from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.core.services.external.session import service_session


class ExternalService:
    """
    A third party web service BeetstreamNext talks to.
    """

    label: str = ''        # Service name for logs and messages
    timeout: float = 8     # sec

    def request(self, method: str, url: str, *, cached: bool = False, **kwargs) -> requests.Response | None:

        kwargs.setdefault('timeout', self.timeout)

        if not cached:
            kwargs.update(expire_after=0, force_refresh=True)

        try:
            response = service_session().request(method, url, **kwargs)
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

    # TODO: refactor the current external services to use this
