# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.13

# Builder

FROM python:${PYTHON_VERSION}-slim AS builder

RUN pip install --no-cache-dir uv==0.12.10

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

ARG EXTRAS=all

COPY pyproject.toml uv.lock README.md ./
COPY beetsplug ./beetsplug

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --extra "${EXTRAS}"

ARG BEETS_VERSION
RUN if [ -n "$BEETS_VERSION" ]; then \
        uv pip install --python .venv/bin/python "beets==${BEETS_VERSION}"; \
    fi

# Runtime

FROM python:${PYTHON_VERSION}-slim AS runtime

ARG WITH_MPV=false
ARG WITH_DEBUG_TOOLS=false
RUN apt-get update && apt-get install --no-install-recommends -y \
        ffmpeg \
        util-linux \
        tini \
        $( [ "$WITH_MPV" = "true" ] && echo mpv ) \
        $( [ "$WITH_DEBUG_TOOLS" = "true" ] && echo curl wget iputils-ping dnsutils netcat-openbsd iproute2 ) \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 beetstream && \
    useradd --uid 1000 --gid beetstream --home-dir /home/beetstream \
        --create-home --shell /usr/sbin/nologin beetstream

WORKDIR /app
COPY --from=builder --chown=beetstream:beetstream /app/.venv /app/.venv
COPY docker-entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    HOME=/home/beetstream \
    XDG_CACHE_HOME=/cache \
    BSN_DB_PATH=/config/beetstreamnext.db \
    BEETSDIR=/config/beets

RUN mkdir -p /config /cache && chown -R beetstream:beetstream /config /cache
VOLUME ["/config", "/cache"]

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "\
import os, pathlib, urllib.request; \
p = pathlib.Path(os.environ.get('XDG_CACHE_HOME', '/cache')) / 'beetstreamnext' / 'runtime_port'; \
port = p.read_text().strip() if p.is_file() else '8080'; \
urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=3)" \
    || exit 1

ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["run"]
