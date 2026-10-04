from __future__ import annotations

import flask


def modal_error(message: str, result_id: str) -> flask.Response:
    """Error for a form inside a modal: retargets the response from the list to the modal's message slot."""
    response = flask.make_response(flask.render_template('partials/action_result.html', message=message, ok=False))
    response.headers['HX-Retarget'] = f'#{result_id}'
    response.headers['HX-Reswap'] = 'innerHTML'
    return response
