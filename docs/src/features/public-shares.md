# Public shares

Users with the `shareRole` permission can generate public share links for a song, album, or playlist.

**BeetstreamNext** creates a public landing page with a secure download endpoint, accessible without needing the recipient to have an account or Subsonic client.

Active shares can be revoked at any time from the admin panel's [Shares tab](../usage/webui.md#public-shares).

## Public routes

Everything that works without an account is a _public route_:

- share pages and downloads (`/share/...`)
- tokenised stream and image links (`/tokenised-stream/...`, `/tokenised-image/...`)
- the now-playing cover (`/now-playing/cover`)
- the health check (`/healthz`), the homepage (`/`) and the static files they use

Everything else (the Subsonic API, login, account pages, admin panel, control API) needs an account.

## Public hostname

You can expose the public shares on a _different_ hostname than what **BeetstreamNext** binds to locally:

For example, you access the admin panel via `https://admin.example.com` and the Subsonic API and account pages via `https://music.example.com`, but you want to allow unauthenticated users to download public shares from `https://public.example.com`.

You can set `external_hostname` so that share links use the correct public URL instead of the internal bind address:

```yaml
beetstreamnext:
  external_hostname: public.example.com
```

## Listener scopes

Every listener of **BeetstreamNext** has a _scope_ that sets which routes it serves. Any other route returns 404.

| Scope    | Serves                                                            |
|----------|-------------------------------------------------------------------|
| `public` | the public routes                                                 |
| `users`  | the public routes, the Subsonic API, login and account pages      |
| `admin`  | everything, including the admin panel and the control API         |

By default, a single listener has the `admin` scope. To give addresses or ports a smaller scope, use:

### By address: `public_hosts` and `admin_hosts`

On a machine with several addresses, [`public_hosts`](../configuration.md#public_hosts) and [`admin_hosts`](../configuration.md#admin_hosts) pick the ones that serve the `public` and `admin` scopes. They must also be in `host`. The remaining addresses have the `users` scope (or `admin`, if neither `admin_hosts` nor `admin_port` is set):

```yaml
beetstreamnext:
  host: [100.70.0.5, 127.0.0.1, 192.168.0.10]
  public_hosts: [192.168.0.10]
  admin_hosts: [127.0.0.1]
```

| Address        | Scope    |
|----------------|----------|
| `192.168.0.10` | `public` |
| `100.70.0.5`   | `users`  |
| `127.0.0.1`    | `admin`  |

`host` must list addresses explicitly (no `0.0.0.0` or `::`) when it is combined with these settings.

> [!NOTE]
> The first-run setup page is part of the admin panel, so it is served on `admin` listeners.

### By port: `public_port` and `admin_port`

With a single address, [`public_port`](../configuration.md#public_port) and [`admin_port`](../configuration.md#admin_port) open extra ports for the `public` and `admin` scopes.

`port` then has the `users` scope:

```yaml
beetstreamnext:
  port: 8080
  public_port: 8081
  admin_port: 8082
```

Every address in `host` listens on all three ports: point your reverse proxy's public hostname at `8081`, the Subsonic hostname at `8080`, and the admin hostname at `8082`.

See [Traefik with Docker](../reverse-proxy.md#traefik-with-docker) for a complete example.

### Using hosts and ports together

Addresses in `public_hosts` and `admin_hosts` get their scope on `port`, and `public_port` and `admin_port` cover the remaining addresses of `host`.

For example, with this config:

```yaml
beetstreamnext:
  host: [100.70.0.5, 192.168.0.10]
  public_hosts: [192.168.0.10]
  port: 8080
  public_port: 8081
```

| Listens on          | Scope    |
|---------------------|----------|
| `100.70.0.5:8080`   | `admin`  |
| `100.70.0.5:8081`   | `public` |
| `192.168.0.10:8080` | `public` |

### Good to know

- These settings need a restart. With a Unix socket or in debug mode there is a single `admin` listener and they are ignored.

- Public listeners have their own thread pool (up to 8 threads), so heavy public traffic can't starve the others.

---

See [`external_hostname`](../configuration.md#external_hostname) in the configuration reference.
