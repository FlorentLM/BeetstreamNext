import flask

from .. import public_bp

from beetsplug.beetstreamnext.public.tokenizer import stream_tokens
from beetsplug.beetstreamnext.utils.general import send_file


@public_bp.route('/tokenised-stream/<token>/<filename>')
def tokenised_stream(token: str, filename: str) -> flask.Response:

    path = stream_tokens.resolve(token)
    if not path:
        flask.abort(404)

    response = send_file(path)
    if response is None:
        flask.abort(404)
    return response
