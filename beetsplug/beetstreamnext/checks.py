from __future__ import annotations

from flask import Flask
from flask_wtf.csrf import CSRFProtect
from jinja2 import nodes

from beetsplug.beetstreamnext.constants import BLUEPRINTS, BlueprintPolicy, Scope
from beetsplug.beetstreamnext.core.config.settings_schema import SETTINGS_SCHEMA
from beetsplug.beetstreamnext.core.config.store import coerce_setting


##

OPEN_ENDPOINTS = frozenset({
    'admin.route_setup',
    'control_api.route_trigger_import',
})

CSRF_EXEMPT_BLUEPRINTS = frozenset({
    'subsonic',
    'control_api'
})


##

def route_collisions(app: Flask) -> None:
    """Check if two routes share the same path + method."""

    seen = {}

    for rule in app.url_map.iter_rules():

        for method in (rule.methods or set()) - {'HEAD', 'OPTIONS'}:
            key = (rule.rule, method)
            if key in seen:
                raise RuntimeError(
                    f"Route collision: {method} {rule.rule} registered by both "
                    f"'{seen[key]}' and '{rule.endpoint}'"
                )

            seen[key] = rule.endpoint


def auth_coverage(app: Flask) -> None:
    """Check if a (not explicitly open) view is missing a auth decorator, and if every blueprint has a policy."""

    unprotected = sorted(
        endpoint for endpoint, view in app.view_functions.items()
        if endpoint not in OPEN_ENDPOINTS
        and not BLUEPRINTS.get(endpoint.partition('.')[0], BlueprintPolicy(Scope.ADMIN)).self_authenticated
        and not getattr(view, 'requires_auth', False)
    )

    if unprotected:
        raise RuntimeError(f"Views without auth decorator: {', '.join(unprotected)}.")

    blueprints = {endpoint.partition('.')[0] for endpoint in app.view_functions}

    unlisted = sorted(blueprints - BLUEPRINTS.keys())
    if unlisted:
        raise RuntimeError(f"Blueprints missing from BLUEPRINTS in constants.py: {', '.join(unlisted)}.")

    stale = sorted(
        [e for e in OPEN_ENDPOINTS if e not in app.view_functions] +
        [b for b in BLUEPRINTS if b not in blueprints]
    )
    if stale:
        raise RuntimeError(f"Startup checks reference unknown element{'s' if len(stale) > 1 else ''}: {', '.join(stale)}")


def csrf_exemptions(csrf: CSRFProtect) -> None:
    """Check the CSRF-exempt blueprints."""

    exempt_blueprints = {bp.name for bp in getattr(csrf, '_exempt_blueprints', ())}
    exempt_views = set(getattr(csrf, '_exempt_views', ()))

    if exempt_blueprints != CSRF_EXEMPT_BLUEPRINTS or exempt_views:
        raise RuntimeError(
            f"Unexpected CSRF exemptions: blueprints={sorted(exempt_blueprints)}, views={sorted(exempt_views)}; "
            f"expected only blueprints={sorted(CSRF_EXEMPT_BLUEPRINTS)}"
        )


def settings_schema() -> None:
    """
    Check if a setting has an unknown type, a default value
    rejected by its type/choices/validator, or a duplicate env var.
    """

    problems = []
    env_vars = {}

    for key, spec in SETTINGS_SCHEMA.items():

        if 'default' not in spec:
            problems.append(f"{key}: no default")
            continue

        try:
            default = coerce_setting(spec['default'], spec.get('type'))

        except (ValueError, TypeError) as e:
            problems.append(f"{key}: wrong type for default {spec['default']!r} ({e})")
            continue

        if 'choices' in spec and default not in spec['choices']:
            problems.append(f"{key}: default {default!r} is not in {spec['choices']}")

        if 'validator' in spec:
            try:
                spec['validator'](default)
            except (ValueError, TypeError) as e:
                problems.append(f"{key}: default {default!r} fails its validator ({e})")

        if env_var := spec.get('env_var'):
            if env_var in env_vars:
                problems.append(f"{key}: env var {env_var} already used by {env_vars[env_var]}")

            env_vars[env_var] = key

    if problems:
        raise RuntimeError('Invalid settings schema:\n  ' + '\n  '.join(problems))


def template_endpoints(app: Flask) -> None:     # Used by integrated tests
    """Check if a template calls url_for() on an endpoint that doesn't exist."""

    env = app.jinja_env

    missing = sorted(
        f"{name}: {call.args[0].value}"
        for name in env.list_templates()
        for call in env.parse(env.loader.get_source(env, name)[0]).find_all(nodes.Call)
        if getattr(call.node, 'name', None) == 'url_for'
        and call.args and isinstance(call.args[0], nodes.Const)
        and call.args[0].value not in app.view_functions
    )

    if missing:
        raise RuntimeError(f"Templates reference unknown endpoint{'s' if len(missing) > 1 else ''}: {', '.join(missing)}")


def startup_checks(app: Flask, csrf: CSRFProtect) -> None:

    route_collisions(app)
    auth_coverage(app)
    csrf_exemptions(csrf)
    settings_schema()