# Web UI

The Web UI is mostly self-explanatory so this is just a quick tour, plus the handful of things that are only reachable through it (that is, not exposed to Subsonic clients).

## Public homepage

Your configured `http://<host>:<port>/` (or your `external_hostname`) shows a public homepage.

> **Note:** The "Login" button will only show up on the host allowed by `admin_hostname` if this setting is set.

If `public_now_playing` is enabled, a card displays the currently playing track (_off_ by default).

> 🖼️ *Screenshot: public homepage*

## Public shares

TODO

## Admin dashboard

The dashboard is organized into tabs. Most settings are editable live, those that require a server restart to take effect are clearly marked.

A setting that is already pinned by a CLI flag, an environment variable, or a `config.yaml` value shows as _locked_/_disabled_ from the Web UI.

See [Configuration reference](../configuration.md) for the precedence rules.

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

Add/refresh/delete podcast subscriptions and internet radio stations server-wide including radio [station discovery](../features/radio-and-podcasts.md#internet-radio) when `enable_radio_discovery` is on.

> 🖼️ *Screenshot: Radios*

> 🖼️ *Screenshot: Podcasts*

### Shares

View and revoke any active [public share](../features/public-shares.md).

### Chat moderation

View the full chat log, edit/delete any user's message for moderation, or post server announcements.

> 🖼️ *Screenshot: chat moderation panel*

### Beets

Interact with the Beets installation: view/update Beets' `config.yaml` and run imports.

#### Interactive import

Enter a directory, and start a `beet import` on it in an interactive terminal-like view, similar to [**Betanin**](https://github.com/sentriz/betanin).

> 🖼️ *Screenshot: Import*

#### Pinned import folders

Pin the folders you regularly import from (a _downloads_ directory for instance) so they can be imported _non-interactively_ from Subsonic clients (quiet mode: beets follows your config for `quiet_fallback`, `timid`, etc).

See [`startScan`](../api-coverage.md) endpoint.

> **Note:** Imports that modify files on disk (writing tags, copying or moving files) need [`allow_disk_writes`](../configuration.md#library--metadata) to be enabled, unless your Beets config has `write`, `copy` and `move` all disabled.

#### Watched folders (auto-import)

When a pinned folder is flagged as **Watch**, new audio files dropped into it are imported automatically.

   - This needs the global [`import_watch_enabled`](../configuration.md#library--metadata) setting to be enabled.

   - Watched folders are polled every 30 seconds.

   - A folder is only imported once its contents have stopped changing for [`import_watch_settle`](../configuration.md#library--metadata) (60 sec by default). This is to avoid auto-importing half-copied or half-downloaded albums.

   - Watched imports are always `quiet` and `incremental`.

   - Skipping rules follow your Beets configuration.

### Maintenance

Trigger a library health scan (see [Encoding errors detection & self-healing streams](../features/streaming.md#encoding-errors-detection--self-healing-streams)), cleanup BeetstreamNext's database, clear disk caches, and view the server logs.




