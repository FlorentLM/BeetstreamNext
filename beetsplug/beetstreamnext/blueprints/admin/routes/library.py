from __future__ import annotations

import flask

from .. import admin_bp, admin_required

from beetsplug.beetstreamnext.application import with_app_context
from beetsplug.beetstreamnext.core.library import browse


@admin_bp.route('/library/rows', methods=['GET'])
@admin_required
@with_app_context
def route_library_rows() -> flask.Response:

    r = flask.request.args
    size = max(1, min(r.get('size', default=50, type=int), browse.MAX_PAGE_SIZE))

    # Tabulator's header filters
    filters = tuple(
        (r[f'filter[{i}][field]'], r.get(f'filter[{i}][type]', 'like'), r.get(f'filter[{i}][value]', '')[:200])
        for i in range(browse.MAX_FILTERS) if f'filter[{i}][field]' in r
    )

    try:
        rows, total = browse.LibraryView().rows(
            q=r.get('q', default='')[:200],
            filters=filters,
            sort=r.get('sort[0][field]', default=''),
            descending=r.get('sort[0][dir]') == 'desc',
            page=r.get('page', default=1, type=int),
            size=size,
        )
    except ValueError:
        flask.abort(400)

    return flask.jsonify({'data': rows, 'last_row': total, 'last_page': max(1, -(-total // size))})
