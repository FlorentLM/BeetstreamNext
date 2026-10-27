from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Tuple

from beetsplug.beetstreamnext.constants import LOOPBACK_IPS, UNIX_SOCKET_PREFIX, Scope
from beetsplug.beetstreamnext.core.runtime.logging import bsn_logger
from beetsplug.beetstreamnext.utils.net import bind_netloc, is_wildcard_address, parse_host


@dataclass(frozen=True)
class Listeners:
    """
    What to listen on (as `host:port` strings) for each scope.
    """

    by_scope: Dict[Scope, List[str]]

    @property
    def all(self) -> List[str]:
        return [netloc for netlocs in self.by_scope.values() for netloc in netlocs]

    @property
    def probe(self) -> str:
        """
        `host:port` that the healthcheck can access.
        """
        listening = [parse_host(netloc) for netloc in self.all]
        target = next((p for p in listening if p.host in LOOPBACK_IPS), listening[0])
        host = target.host
        if is_wildcard_address(host):
            host = '::1' if ':' in host else '127.0.0.1'
        return bind_netloc(host, target.port)


class ListenerScope:
    """
    WSGI wrapper to tag requests with a listener's scope.
    (unix socket requests don't go through one so are not tagged)
    """

    KEY = 'bsn.scope'

    def __init__(self, wsgi_app: Callable, scope: Scope) -> None:
        self._wsgi_app = wsgi_app
        self._scope = scope

    def __call__(self, environ, start_response):
        environ[self.KEY] = self._scope
        return self._wsgi_app(environ, start_response)


def split_listeners(
        hosts: List[str],
        public_hosts: Iterable[str],
        admin_hosts: Iterable[str]
    ) -> Tuple[List[str], List[str], List[str]]:
    """
    Split bind addresses into (base, public, admin).
    """

    public_wanted = {h.lower() for h in public_hosts}
    admin_wanted = {h.lower() for h in admin_hosts}

    if not public_wanted and not admin_wanted:
        return list(hosts), [], []

    if any(h.startswith(UNIX_SOCKET_PREFIX) for h in hosts):
        raise ValueError('`public_hosts` and `admin_hosts` cannot be used with a unix socket.')

    if public_wanted & admin_wanted:
        raise ValueError(f"Can't be in both `public_hosts` and `admin_hosts`: {', '.join(sorted(public_wanted & admin_wanted))}.")

    listed = {h.lower() for h in hosts}

    for name, wanted in (('public_hosts', public_wanted), ('admin_hosts', admin_wanted)):
        if wanted - listed:
            raise ValueError(f"`{name}` entries must also be listed in `host`: {', '.join(sorted(wanted - listed))}.")

    public = [h for h in hosts if h.lower() in public_wanted]
    admin = [h for h in hosts if h.lower() in admin_wanted]
    base = [h for h in hosts if h.lower() not in public_wanted | admin_wanted]

    if sum(map(bool, (base, public, admin))) > 1 and any(is_wildcard_address(h) for h in hosts):
        raise ValueError(
            'A wildcard address (0.0.0.0, ::) in `host` would overlap the other address(es). '
            'List the addresses to listen on explicitly.'
        )

    return base, public, admin


def resolve_listeners(
        hosts: List[str],
        port: int,
        public_hosts: Iterable[str] = (),
        public_port: int = 0,
        admin_hosts: Iterable[str] = (),
        admin_port: int = 0,
        can_bind: Callable[[str, int], bool] = lambda host, port: True,
    ) -> Listeners:
    """
    Determine what to listen on from `host`, `port` and the `public_*` and `admin_*` settings.

    Raises ValueError if the setup can't work.
    """

    ports = [p for p in (port, public_port, admin_port) if p]
    if len(ports) != len(set(ports)):
        raise ValueError('`port`, `public_port` and `admin_port` must all be different.')

    base, public, admin = split_listeners(hosts, public_hosts, admin_hosts)

    by_scope = {
        Scope.PUBLIC: [bind_netloc(h, port) for h in public],
        Scope.USERS: [],
        Scope.ADMIN: [bind_netloc(h, port) for h in admin],
    }
    by_scope[Scope.USERS if (admin or admin_port) else Scope.ADMIN] += [bind_netloc(h, port) for h in base]

    for scope, scope_port in ((Scope.PUBLIC, public_port), (Scope.ADMIN, admin_port)):
        for host in base if scope_port else ():
            if can_bind(host, scope_port):
                by_scope[scope].append(bind_netloc(host, scope_port))
            else:
                bsn_logger.warning(f"Can't bind '{host}' on {scope.name.lower()}_port {scope_port}. Skipping it.")

    return Listeners({scope: netlocs for scope, netlocs in by_scope.items() if netlocs})