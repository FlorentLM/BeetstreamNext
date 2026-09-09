# Public shares

Users with the `shareRole` permission can generate public share links for a song, album, or playlist. BeetstreamNext creates a public landing page with a secure download endpoint, accessible without needing the recipient to have an account or Subsonic client.

Active shares can be revoked at any time from the admin panel's [Shares tab](../usage/webui.md#public-shares).

## Public hostname

If BeetstreamNext is reachable from the internet under a different hostname than what it binds to locally (e.g. behind a reverse proxy), set `external_hostname` so that share links use the correct public URL instead of the internal bind address:

```yaml
beetstreamnext:
  external_hostname: music.example.com
```

---

See [`external_hostname`](../configuration.md#external_hostname) in the configuration reference.
