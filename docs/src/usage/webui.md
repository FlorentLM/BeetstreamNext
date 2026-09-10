# Web UI

The Web UI is mostly self-explanatory so this is just a quick tour, plus the handful of things that are only reachable through it (that is, not exposed to Subsonic clients).

## Public homepage

Your configured `http://<host>:<port>/` (or your `external_hostname`) shows a public homepage.

> **Note:** The "Login" button will only show up on the host allowed by `admin_hostname` if this setting is set.

If `public_now_playing` is enabled, a card displays the currently playing track (**off** by default).

> 🖼️ *Screenshot: public homepage*

## Public shares

TODO

## Admin dashboard

The dashboard is organized into tabs. Most settings are editable live, while some require a server restart to take effect. A setting that's already pinned by a CLI flag, environment variable, or `config.yaml` shows as locked/disabled here — see [Configuration reference](../configuration.md) for the precedence rules.

### Users

Create/update/delete users, toggle their [roles](../features/accounts-and-permissions.md#roles), regenerate an API key, and manage avatars.

### Server

Every [Server & Network](../configuration.md#server--network) related setting.

> 🖼️ *Screenshot: Server settings page*

### Library

Every [Library & Metadata](../configuration.md#library--metadata) related setting.

> 🖼️ *Screenshot: Library settings page*

### Audio

Every [Audio & Jukebox](../configuration.md#audio--jukebox) related setting.

> 🖼️ *Screenshot: Audio settings page*

### Security

Live view of the current _IP allow/deny_ lists and _rate-limit_ state (see [Security](../features/security.md)). Allows adding/removing entries, and clearing rate-limit buckets.

> 🖼️ *Screenshot: Security tab*

### Podcasts & radio

Add/refresh/delete podcast subscriptions and internet radio stations server-wide

[//]: # (including radio [station discovery]&#40;../features/radio-and-podcasts.md#internet-radio&#41; when `enable_radio_discovery` is on.)

> 🖼️ *Screenshot: Radios*

> 🖼️ *Screenshot: Podcasts*

### Shares

View and revoke any active [public share](../features/public-shares.md).

### Chat moderation

View the full chat log, edit/delete any user's message for moderation, or post server announcements.

> 🖼️ *Screenshot: chat moderation panel*

### Beets

Interact with the Beets installation: view/update Beets' `config.yaml`, trigger a Beets library rescan and view its output log.

### Maintenance

Trigger a library health scan (see [Encoding errors detection & self-healing streams](../features/streaming.md#encoding-errors-detection--self-healing-streams)), cleanup BeetstreamNext's database, clear disk caches, and view the server logs.




