# Security

## Access controls

- **IP allow/deny lists**: `ip_whitelist`/`ip_blacklist`, they accept individual IPs or CIDR ranges.
- **Admin panel host restriction**: `admin_hostname` restricts the admin panel to a specific hostname (loopback is always allowed regardless).
- **Trusted `Host` headers**: `trusted_hosts` controls which `Host` header values are accepted.
- **Legacy authentication**: `legacy_auth` controls whether the older MD5-token/cleartext Subsonic authentication mode is accepted. Many clients still need it. API-key auth authentication works regardless of this setting.

## Adaptive login rate-limiting

Two independent buckets slow down brute-force attempts:

- **Per-pair of _(IP, username)_**: `rate_limit_max_failures` failed attempts within `rate_limit_block_window` seconds blocks that specific _(IP, username)_ pair.
- **Per-IP**: `rate_limit_ip_max_failures` failed attempts from an IP (using *any* username) within `rate_limit_ip_block_window` seconds blocks that IP. This catches attackers rotating usernames to dodge the per-user limit above.

Both buckets's states are visible and can be cleared from the admin panel's [Security tab](../usage/webui.md#security).

---

See the [configuration reference](../configuration.md) for any of the settings mentioned here.