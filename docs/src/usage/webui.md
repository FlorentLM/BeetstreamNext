# Web UI

The Web UI is mostly self-explanatory so this is just a quick tour, plus the handful of things that are only reachable through it (that is, not exposed to Subsonic clients).

Hover on the screenshot to see Light/Dark modes.

## Public homepage

Your configured `http://<host>:<port>/` (or your `external_hostname`) shows a public homepage.

If `public_now_playing` is enabled, a card displays the currently playing track (_off_ by default).

<span class="screenshot-pair"><img class="screenshot light" src="../images/light/screenshot_home.png" alt="Public homepage"><img class="screenshot dark" src="../images/dark/screenshot_home.png" alt="" aria-hidden="true"></span>

## Public shares

TODO

## Admin dashboard

The dashboard is organized into tabs. Most settings are editable live, those that require a server restart to take effect are clearly marked.

A setting that is already pinned by a CLI flag, an environment variable, or a `config.yaml` value shows as _locked_/_disabled_ from the Web UI.

See [Configuration reference](../configuration.md) for the precedence rules.

### Users

Create/update/delete users, toggle their [roles](../features/accounts-and-permissions.md#roles), manage avatars, and see or revoke their [app passwords](../features/app-passwords.md).

<span class="screenshot-pair"><img class="screenshot light" src="../images/light/screenshot_users.png" alt="Users"><img class="screenshot dark" src="../images/dark/screenshot_users.png" alt="" aria-hidden="true"></span>

### Connections

Your existing [app passwords](../features/app-passwords.md) and signed-in web sessions.

<span class="screenshot-pair"><img class="screenshot light" src="../images/light/screenshot_connections.png" alt="Connections"><img class="screenshot dark" src="../images/dark/screenshot_connections.png" alt="" aria-hidden="true"></span>

### Server

Every [Server & Network](../configuration.md#server--network) related setting.

<span class="screenshot-pair"><img class="screenshot light" src="../images/light/screenshot_server.png" alt="Server settings"><img class="screenshot dark" src="../images/dark/screenshot_server.png" alt="" aria-hidden="true"></span>

### Library

Every [Library & Metadata](../configuration.md#library--metadata) related setting.

<span class="screenshot-pair"><img class="screenshot light" src="../images/light/screenshot_library.png" alt="Library settings"><img class="screenshot dark" src="../images/dark/screenshot_library.png" alt="" aria-hidden="true"></span>

### Audio

Every [Audio & Jukebox](../configuration.md#audio--jukebox) related setting.

<span class="screenshot-pair"><img class="screenshot light" src="../images/light/screenshot_audio.png" alt="Audio settings"><img class="screenshot dark" src="../images/dark/screenshot_audio.png" alt="" aria-hidden="true"></span>

### Security

Live view of the current _IP allow/deny_ lists and _rate-limit_ state (see [Security](../features/security.md)). Allows adding/removing entries, and clearing rate-limit buckets.

<span class="screenshot-pair"><img class="screenshot light" src="../images/light/screenshot_security.png" alt="Security tab"><img class="screenshot dark" src="../images/dark/screenshot_security.png" alt="" aria-hidden="true"></span>

### Podcasts & radio

Add/refresh/delete podcast subscriptions and internet radio stations.

[Radio discovery](../features/radio-and-podcasts.md#internet-radio) (when `enable_radio_discovery` is on) allows you to find new stations.

<span class="screenshot-pair"><img class="screenshot light" src="../images/light/screenshot_radios.png" alt="Radio stations"><img class="screenshot dark" src="../images/dark/screenshot_radios.png" alt="" aria-hidden="true"></span>

<span class="screenshot-pair"><img class="screenshot light" src="../images/light/screenshot_podcasts.png" alt="Podcasts"><img class="screenshot dark" src="../images/dark/screenshot_podcasts.png" alt="" aria-hidden="true"></span>

### Artists

Browse, search, add or replace artist images, and fetch the missing ones from external services (see [Library augmentation](../features/library-augmentation.md)).

<span class="screenshot-pair"><img class="screenshot light" src="../images/light/screenshot_artists.png" alt="Artist images"><img class="screenshot dark" src="../images/dark/screenshot_artists.png" alt="" aria-hidden="true"></span>

### Shares

View, [edit](../features/public-shares.md#share-options) and revoke any active [public share](../features/public-shares.md).

<span class="screenshot-pair"><img class="screenshot light" src="../images/light/screenshot_shares.png" alt="Shares"><img class="screenshot dark" src="../images/dark/screenshot_shares.png" alt="" aria-hidden="true"></span>

### Chat moderation

View the full chat log, edit/delete any user's message for moderation, or post server announcements.

<span class="screenshot-pair"><img class="screenshot light" src="../images/light/screenshot_chat.png" alt="Chat moderation"><img class="screenshot dark" src="../images/dark/screenshot_chat.png" alt="" aria-hidden="true"></span>

### Beets

Interact with the Beets installation: view Beets' `config.yaml` (editing it needs to be [allowed at server startup](../features/beets-import.md#editing-the-beets-config)) and run imports (interactive, pinned and watched folders).

See [Beets import](../features/beets-import.md) for the details.

<span class="screenshot-pair"><img class="screenshot light" src="../images/light/screenshot_beets.png" alt="Beets configuration and import"><img class="screenshot dark" src="../images/dark/screenshot_beets.png" alt="" aria-hidden="true"></span>

### Notifications

Create and manage [notifications](../features/notifications.md) sent through Apprise (imports, blocked logins, health scans, new shares...).

<span class="screenshot-pair"><img class="screenshot light" src="../images/light/screenshot_notifications.png" alt="Notifications"><img class="screenshot dark" src="../images/dark/screenshot_notifications.png" alt="" aria-hidden="true"></span>

### Maintenance

Trigger a library health scan (see [Encoding errors detection & self-healing streams](../features/streaming.md#encoding-errors-detection--self-healing-streams)), cleanup BeetstreamNext's database, clear disk caches, and view the server logs.

<span class="screenshot-pair"><img class="screenshot light" src="../images/light/screenshot_maintenance.png" alt="Maintenance"><img class="screenshot dark" src="../images/dark/screenshot_maintenance.png" alt="" aria-hidden="true"></span>
