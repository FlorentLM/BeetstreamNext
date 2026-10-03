# Public shares

Users with the `shareRole` permission can generate public share links for a song, album, or playlist.

**BeetstreamNext** creates a public landing page with a secure download endpoint, accessible without needing the recipient to have an account or Subsonic client.

Active shares can be revoked at any time from the admin panel's [Shares tab](../usage/webui.md#public-shares).

## Public hostname

You can expose the public shares on a _different_ hostname than what **BeetstreamNext** binds to locally:

For example, you access the admin panel and the Subsonic API via `https://beetstreamnext.internal.example.com` but you want to allow unauthenticated users to download public shares from `https://shares.example.com`.

You can set `external_hostname` so that share links use the correct public URL instead of the internal bind address:

```yaml
beetstreamnext:
  external_hostname: shares.example.com
```

---

See [`external_hostname`](../configuration.md#external_hostname) in the configuration reference.
