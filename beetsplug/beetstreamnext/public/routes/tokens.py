import flask

from .. import public_bp

from beetsplug.beetstreamnext.public.tokenizer import stream_tokens
from beetsplug.beetstreamnext.utils.general import send_file


@public_bp.route('/tokenised-stream/<token>/<filename>')
def tokenised_stream(token: str, filename: str) -> flask.Response:
    """
    Unauthenticated but token-gated stream route. Used by Sonos jukebox backend.

    'filename' arg is only there for backends that need a recognisable extension in the URL
    (Sonos rejects AddURIToQueue with UPnP error 804 on an extension-less URL), but the token is sufficient
    to resolve the file.
    """

    path = stream_tokens.resolve(token)
    if not path:
        flask.abort(404)

    response = send_file(path)
    if response is None:
        flask.abort(404)
    return response
