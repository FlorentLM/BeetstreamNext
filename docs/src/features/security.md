# Security

## Access controls

- **IP allow/deny lists**: [`ip_whitelist`](../configuration.md#ip_whitelist)/[`ip_blacklist`](../configuration.md#ip_blacklist), they accept individual IPs or CIDR ranges.

- **Listener scopes**: each listener has a scope (`public`, `users` or `admin`) that sets which routes it serves, based on the address and port it listens on. See [Listener scopes](./public-shares.md#listener-scopes).
  - [`public_hosts`](../configuration.md#public_hosts) and [`public_port`](../configuration.md#public_port) give listeners the `public` scope ([public routes](./public-shares.md#public-routes)).
  
  - [`admin_hosts`](../configuration.md#admin_hosts) and [`admin_port`](../configuration.md#admin_port) give listeners the `admin` scope (admin panel and control API).
  
  - The other addresses have the `users` scope.
  
  - **Remark:** By default, a single listener serves everything.

- **Trusted `Host` headers**: [`trusted_hosts`](../configuration.md#trusted_hosts) controls which `Host` header values are accepted.
  - **Remark:** If the server is reachable beyond your own machine, it is recommended to set this. You can also use [`external_hostname`](../configuration.md#external_hostname) for share links.
  
  - A `Host` header that isn't just a `hostname[:port]` (scheme, path, `user@`, spaces, control characters...) is always refused (with HTTP 400).

- **Legacy authentication**: [`legacy_auth`](../configuration.md#legacy_auth) controls whether the older MD5-token/cleartext Subsonic authentication mode is accepted. Many clients still need it.
  - **Remark:** API-key authentication is always available regardless of this setting, so clients that support `apiKey` can use an [app password](./app-passwords.md) with this turned off.
  
  - **Remark:** The Subsonic protocol sends credentials in the URL query string, so they can end up in **reverse-proxy access logs**. See [Credentials in proxy logs](../reverse-proxy.md#credentials-in-proxy-logs).

- **App passwords**: clients should use [app passwords](./app-passwords.md), which can be revoked, limited to read-only access and set to expire, instead of the account password.

- **User management over the API**: [`api_user_management`](../configuration.md#api_user_management) controls whether admins can create, update and delete users, and change other users' passwords, through the Subsonic API. It is off by default. The admin panel is not affected.

## Beets config protection

The Beets config can load plugins and run commands, so editing it from the admin panel has some restrictions:

- [`allow_beets_config_edit`](../configuration.md#allow_beets_config_edit) must be enabled before server runs (it can't be changed from the Web UI).

- **Code-execution settings are blocked**: even when editing is on, the panel refuses to add or modify the `inline` and `hook` plugins, `pluginpath`, `item_fields`/`album_fields`, and options holding a command or binary (`convert.command`, `play.command`, `keyfinder.bin`, etc).

    Settings already present in the file are honoured as long as they are unchanged.

    See [Blocked settings](./beets-import.md#blocked-settings) for the full list.

- **Disk writes need opt-in**: imports that would modify files (tags, copy/move, or plugins such as `scrub`, `embedart` or `fetchart` in `auto` mode) require [`allow_disk_writes`](../configuration.md#allow_disk_writes).

    See [Disk writes](./beets-import.md#disk-writes).

> [!NOTE]
> These are guard rails against a compromised or careless admin session, not a sandbox. Someone who has write access to the config file on the server can obviously still change them.

## Adaptive login rate-limiting

Two independent buckets slow down brute-force attempts:

- **Per-pair of _(IP, username)_**: [`rate_limit_max_failures`](../configuration.md#rate_limit_max_failures) failed attempts within [`rate_limit_block_window`](../configuration.md#rate_limit_block_window) seconds blocks that specific _(IP, username)_ pair.

- **Per-IP**: [`rate_limit_max_failures`](../configuration.md#rate_limit_max_failures) failed attempts from an IP (using *any* username) within [`rate_limit_block_window`](../configuration.md#rate_limit_block_window) seconds blocks that IP. This catches attackers rotating usernames to dodge the per-user limit above.

- **Tokenised URLs** (the unauthenticated stream URLs handed to Sonos/Chromecast speakers, and the image links in artist/album info) stop working after 12 hours (streams) or 24 hours (images) without being used. A speaker playing a queue or a client re-fetching an image keeps its link alive. Stale links return 404, and the client gets a fresh one the next time it asks.

---

See the [configuration reference](../configuration.md) for any of the settings mentioned here.