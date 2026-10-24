from __future__ import annotations

from typing import Callable, List
import flask


def route_registrar(bp: flask.Blueprint, login_required: Callable) -> Callable:
    """
    For routes exposed by both the admin panel and the account page.
    Only their auth decorator differs.
    """

    def route(rule: str, methods: List[str]) -> Callable:

        def register(view: Callable) -> Callable:
            bp.add_url_rule(
                rule=rule,
                endpoint=f'route_{view.__name__}',
                view_func=login_required(view),
                methods=methods
            )
            return view

        return register

    return route
