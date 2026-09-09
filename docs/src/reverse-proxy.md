# Reverse proxy & CORS

BeetstreamNext reads standard HTTP headers to determine the original client's IP, so putting it behind a reverse proxy is straightforward. Enable the `reverse_proxy` option (see [configuration](./configuration.md#reverse_proxy)) so it trusts those forwarded headers, and set the `proxy_hops` to the number of trusted proxies in front of it.

## Nginx

```nginx
location /beetstreamnext {
    proxy_pass http://127.0.0.1:8080;

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

## Offloading file serving to the proxy

Instead of streaming raw file bytes through the Python process, the reverse proxy can serve them directly. This only takes effect when `reverse_proxy` is enabled, and only helps for direct (non-transcoded) playback/downloads, transcoded streams always go through Python regardless.

Set `sendfile_method` to `x-accel-redirect` (Nginx) or `x-sendfile` (Apache).

### Nginx (`x-accel-redirect`)

Add an internal-only `location` block aliased to your music root, and point `sendfile_internal_prefix` at it (must match on both sides):

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

`/_bsn_internal` is the default for `sendfile_internal_prefix`, so you only need to set it explicitly if you use a different prefix.

### Apache (`x-sendfile`, via `mod_xsendfile`)

```apache
XSendFile On
XSendFilePath /path/to/your/music/
```

```yaml
beetstreamnext:
    sendfile_method: x-sendfile
```

`sendfile_internal_prefix` isn't used with `x-sendfile` (it's Nginx-specific).

## Web clients and CORS

By default, CORS is disabled. Native mobile/desktop apps usually ignore CORS entirely, so you probably don't need to change anything for those.

However, if you want to use a _web-based_ Subsonic player hosted on a _different_ domain than BeetstreamNext, your browser will block the connection unless you explicitly allow that origin:

```yaml
beetstreamnext:
    cors_origins: 'https://music.example.com' # comma-separated list, or '*' for all
```

If you're behind an SSO gateway (Authelia, Authentik, etc.), or the web player is a bit quirky, you might also need:

```yaml
beetstreamnext:
    cors_supports_credentials: true
```

>️ **Warning:** Never set `cors_origins: '*'` together with `cors_supports_credentials: true`. Doing so would allow *any* website you visit to silently interact with your BeetstreamNext server in the background.
