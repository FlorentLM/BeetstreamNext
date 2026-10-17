# Reverse proxy & CORS

**BeetstreamNext** reads standard HTTP headers to determine the original client's IP, so putting it behind a reverse proxy is straightforward.

Enable the `reverse_proxy` option (see [configuration](./configuration.md#reverse_proxy)) so it trusts those forwarded headers, and set the `proxy_hops` to the number of trusted proxies in front of it.

## Unix socket

With the proxy on the same machine (or sharing a volume with the container), **BeetstreamNext** can listen on a unix socket instead of a port.

Set `host` to `unix:/absolute/path/of/socket`:

```
BSN_HOST=unix:/run/beetstreamnext/bsn.sock
```

The socket is created with `660` permissions: add the proxy's user to the group that runs **BeetstreamNext**.

- `reverse_proxy` has to be on, the server refuses to start otherwise: every request via a socket looks local, so the real client addresses can _only_ come from the proxy's forwarded headers.
- It can't be combined with other `host` entries
- It isn't available on Windows, nor in debug mode.
- The folder must exist and be writable by **BeetstreamNext**, and an old socket file from a previous run is replaced.
- In Docker, mount the socket's folder as a volume shared with the proxy.

## Nginx

Example configuration:

```nginx
location /beetstreamnext {
    proxy_pass http://127.0.0.1:8080;  # or http://unix:/run/beetstreamnext/bsn.sock if using a Unix socket

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # If hosting in a subfolder, tell BeetstreamNext what the subfolder is!
    proxy_set_header X-Forwarded-Prefix /beetstreamnext;
}
```

## Caddy 2

Caddy passes all the required headers by default:

```
example.com {
    reverse_proxy 127.0.0.1:8080
}
```
(or `reverse_proxy unix//run/beetstreamnext/bsn.sock` if using a Unix socket)

## Credentials in proxy logs

The Subsonic protocol sends credentials as URL query parameters: `p` (password), `t` and `s` (token and salt), and `apiKey`. Creating or updating a user over the API also puts the new `password` in the query string.

**BeetstreamNext** redacts these from its own logs, but your reverse proxy sees the full URL, and **its access log will contain them in clear text** (and so will any other log or monitoring tool in front of **BeetstreamNext**). Anyone who can read those logs can read your users' passwords and API keys.

To avoid it, either turn access logging off, or log the path only and leave the query string out.

**Nginx**: log `$uri` instead of `$request`/`$request_uri`:

```nginx
log_format bsn '$remote_addr [$time_local] "$request_method $uri" $status $body_bytes_sent';
access_log /var/log/nginx/beetstreamnext.access.log bsn;
```

**Apache**: log `%U` (path only) instead of `%r`:

```apache
LogFormat "%h %t \"%m %U\" %>s %b" bsn
CustomLog logs/beetstreamnext_access.log bsn
```

**Caddy**: replace the sensitive parameters in the log:

```
example.com {
    log {
        format filter {
            wrap json
            fields {
                request>uri query {
                    replace p REDACTED
                    replace t REDACTED
                    replace s REDACTED
                    replace apiKey REDACTED
                    replace password REDACTED
                }
            }
        }
    }
    reverse_proxy 127.0.0.1:8080
}
```

Prefer clients that support [API keys](./features/accounts-and-permissions.md) (`apiKey`) over passwords where possible: an API key can be revoked on its own, whereas a leaked password is also the user's web UI login (and, for admins, the admin panel login).

> [!TIP]
> Avoid using an account with the **Admin** role in a Subsonic client. Create a separate non-admin account for streaming, and keep the admin account for the browser only. That way, whatever ends up in a log can't open the admin panel or manage users.

Also, use HTTPS so the URLs aren't readable on the network.

## Offloading file serving to the proxy

Instead of streaming raw file bytes through the Python process, the reverse proxy can serve them directly.

This only takes effect when `reverse_proxy` is enabled, and only helps for direct (non-transcoded) playback/downloads.

Set `sendfile_method` to `x-accel-redirect` for Nginx, or `x-sendfile` for Apache.

### Nginx (`x-accel-redirect`)

Add an internal-only `location` block aliased to your music root, and point `sendfile_internal_prefix` at it:

```nginx
location /_bsn_internal/ {
    internal;
    alias /path/to/your/music/;
}
```

```yaml
beetstreamnext:
    sendfile_method: x-accel-redirect
    sendfile_internal_prefix: /_bsn_internal
```

`/_bsn_internal` is the default for `sendfile_internal_prefix`.

### Apache (`x-sendfile`, via `mod_xsendfile`)

```apache
XSendFile On
XSendFilePath /path/to/your/music/
```

```yaml
beetstreamnext:
    sendfile_method: x-sendfile
```

`sendfile_internal_prefix` isn't used with `x-sendfile`.

## Full working example: Nginx sidecar for a Docker deployment

Putting everything together, a minimal `nginx.conf` that reverse-proxies a `beetstreamnext` container on the same Docker network _and_ offloads direct file serving via `X-Accel-Redirect`:

```nginx
worker_processes 1;
pid /tmp/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    access_log off;

    server {
        listen 8080;

        location /_bsn_internal/ {
            internal;
            alias /music/;
        }

        location / {
            proxy_pass http://beetstreamnext:8888;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
    }
}
```

Here `8888` can be whatever port (`BSN_PORT`/`port` setting) BeetstreamNext is listening on internally, only Nginx's port needs to be published to the host.

`/music` must be the same music folder mounted into both the `nginx` and `beetstreamnext` containers, with the same relative layout under each mount point (but the absolute host path doesn't need to match).

The matching BeetstreamNext settings:

```yaml
beetstreamnext:
    reverse_proxy: true
    proxy_hops: 2   # The number of proxies in front of BeetstreamNext
    sendfile_method: x-accel-redirect
    sendfile_internal_prefix: /_bsn_internal
```

> [!IMPORTANT]
> For `proxy_hops`, use `1` if the Nginx container is the only proxy, more if Nginx is itself behind a reverse proxy (Traefik, Cloudflare, etc)

## Web clients and CORS

By default, CORS is disabled. Native mobile/desktop apps usually ignore CORS entirely, so you probably don't need to change anything for those.

However, if you want to use a _web-based_ Subsonic player hosted on a _different_ domain than **BeetstreamNext**, your browser will block the connection unless you explicitly allow that origin:

```yaml
beetstreamnext:
    cors_origins: 'https://music.example.com' # comma-separated list, or '*' for all
```

If you're behind an SSO gateway (Authelia, Authentik, etc), or if the web player is a bit quirky, you might also need:

```yaml
beetstreamnext:
    cors_supports_credentials: true
```

> [!CAUTION]
>️ **_Never_** set `cors_origins: '*'` together with `cors_supports_credentials: true`. Doing so would allow *any* website you visit to silently interact with your **BeetstreamNext** server in the background.
