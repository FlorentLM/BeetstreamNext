#!/bin/sh

set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
    [ "$(id -g beetstream)" = "$PGID" ] || groupmod -o -g "$PGID" beetstream
    [ "$(id -u beetstream)" = "$PUID" ] || usermod -o -u "$PUID" beetstream
    chown -R beetstream:beetstream /config /cache
    exec setpriv --reuid beetstream --regid beetstream --init-groups beetstreamnext "$@"
fi

exec beetstreamnext "$@"
