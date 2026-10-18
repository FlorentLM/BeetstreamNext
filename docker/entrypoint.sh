#!/bin/sh

set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

PLUGINS_DIR=/config/plugins
PLUGINS_REQ="$PLUGINS_DIR/requirements.txt"
PLUGINS_INSTALLED="$PLUGINS_DIR/installed"
PLUGINS_STAMP="$PLUGINS_DIR/.requirements.sha256"

as_user() {
    if [ "$(id -u)" = "0" ]; then
        setpriv --reuid beetstream --regid beetstream --init-groups "$@"
    else
        "$@"
    fi
}

# Installs the plugins listed in $BSN_BEETS_PLUGINS (whitespace-separated pip names and/or git+https://... URLs)
# and in /config/plugins/requirements.txt, into /config/plugins/installed
# Only runs when the list changes, failure does not block startup and the previous working set is kept
install_user_plugins() {
    mkdir -p "$TMPDIR"
    combined="$(mktemp)"

    set -f   # no globbing on the specs
    for spec in $BSN_BEETS_PLUGINS; do printf '%s\n' "$spec"; done > "$combined"
    set +f
    [ -f "$PLUGINS_REQ" ] && cat "$PLUGINS_REQ" >> "$combined"

    if ! grep -qvE '^[[:space:]]*(#|$)' "$combined"; then
        # Nothing requested (anymore): uninstall anything installed by an earlier run
        rm -rf "$PLUGINS_INSTALLED" "$PLUGINS_STAMP" "$combined"
        return 0
    fi

    sum="$(sha256sum "$combined" | cut -d' ' -f1)"
    if [ -d "$PLUGINS_INSTALLED" ] && [ "$(cat "$PLUGINS_STAMP" 2>/dev/null)" = "$sum" ]; then
        rm -f "$combined"
        return 0
    fi

    echo "[plugins] Installing user plugins ..."
    constraints="$(mktemp)"
    staging="$PLUGINS_INSTALLED.new"

    # Pin what the image already ships so a plugin can't swap out beets or BSN's dependencies
    uv pip freeze --python /app/.venv/bin/python | grep -v ' @ ' > "$constraints" || true

    mkdir -p "$PLUGINS_DIR"
    rm -rf "$staging"
    if uv pip install --quiet --python /app/.venv/bin/python --target "$staging" \
            --constraint "$constraints" --requirement "$combined"; then
        rm -rf "$PLUGINS_INSTALLED"
        mv "$staging" "$PLUGINS_INSTALLED"
        echo "$sum" > "$PLUGINS_STAMP"
        echo "[plugins] Done."
    else
        rm -rf "$staging"
        echo "[plugins] WARNING: installation failed, starting without the changes (see output above)." >&2
    fi
    rm -f "$constraints" "$combined"
}

if [ "$1" = "--install-plugins" ]; then
    install_user_plugins
    exit 0
fi

if [ "$(id -u)" = "0" ]; then
    [ "$(id -g beetstream)" = "$PGID" ] || groupmod -o -g "$PGID" beetstream
    [ "$(id -u beetstream)" = "$PUID" ] || usermod -o -u "$PUID" beetstream
    chown -R beetstream:beetstream /config /cache
fi

as_user /bin/sh "$0" --install-plugins

if [ "$(id -u)" = "0" ]; then
    exec setpriv --reuid beetstream --regid beetstream --init-groups beetstreamnext "$@"
fi

exec beetstreamnext "$@"
