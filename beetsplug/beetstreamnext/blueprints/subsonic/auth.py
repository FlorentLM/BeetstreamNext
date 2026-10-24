from __future__ import annotations

from typing import TYPE_CHECKING

import flask

from beetsplug.beetstreamnext.application import app
from beetsplug.beetstreamnext.core.accounts.app_passwords import authenticate_api_key, authenticate_legacy
from beetsplug.beetstreamnext.core.accounts.credentials import check_password
from beetsplug.beetstreamnext.utils.text import safe_str

if TYPE_CHECKING:
    from werkzeug.datastructures import CombinedMultiDict


def authenticate_subsonic(flask_req_values: 'CombinedMultiDict'):
    r = flask_req_values
    api_key = r.get('apiKey', default='', type=str)
    user = r.get('u', default='', type=safe_str)
    token = r.get('t', default='', type=str)
    salt = r.get('s', default='', type=str)
    clearpass = r.get('p', default='', type=str)

    if token:
        token = token.lower()  # some clients send uppercase hex
        if len(token) < 32:
            token = token.zfill(32)  # some clients strip leading zeros...

    # API Key (modern)
    if api_key:
        if user or token or salt or clearpass:
            # 43: "Multiple conflicting authentication mechanisms provided."
            return False, 43, None

        matched = authenticate_api_key(api_key, ip=flask.request.remote_addr)
        if matched:
            return True, 0, matched[0]

        # 40: "Wrong username or password."
        return False, 40, None

    # Legacy (MD5 / password)
    else:
        if clearpass and (token or salt):
            # 43: "Multiple conflicting authentication mechanisms provided."
            return False, 43, None

        if not app.config.get('legacy_auth', True):
            # 42: "Provided authentication mechanism not supported."
            return False, 42, None

        if not user:
            # 10: "Required parameter is missing."
            return False, 10, None
        
        if authenticate_legacy(user, token, salt, clearpass, ip=flask.request.remote_addr) is not None:
            return True, 0, user

        success, code, username = check_password(user, token, salt, clearpass)
        return success, code, username
