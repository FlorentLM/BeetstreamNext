# Accounts, permissions & chat

## Authentication

Both authentication schemes from the Subsonic/OpenSubsonic spec are supported:

- **API-key authentication** (recommended): Always available. A user's API key is shown once, on account creation.
- **Legacy MD5-token / cleartext password authentication**: For older clients. Can be enabled server-wide via [`legacy_auth`](../configuration.md#server--network) if you need it.

## Multi-user system

Every user account has their own bookmarks, ratings, favorites, play statistics, and play queues, all synced across whatever devices/clients they use.

BeetstreamNext stores this data separately from your Beets library, since Beets itself has no concept of multi-user data. However, you _can_ have one user's likes/ratings persist inside the Beets library (so they survive outside BeetstreamNext and are visible to other Beets tools): set `ratings_writeback_user` to that username.

## Use roles

Users permissions are [as defined](https://opensubsonic.netlify.app/docs/responses/user/) by the OpenSubsonic/Subsonic spec: controlled by a set of independent role flags. These can be set from the admin panel's Users tab (or `--update-user`/`update-user` on the CLI):

| Role       | Default | Allows                                                          |
|------------|---------|-----------------------------------------------------------------|
| Admin      | off     | Full access to the admin panel and all settings                 |
| Settings   | on      | Changing their own personal settings and password               |
| Stream     | on      | Playing files                                                   |
| Download   | off     | Downloading files                                               |
| Playlists  | on      | Creating and deleting their own playlists                       |
| Comments   | on      | Creating and editing comments and ratings                       |
| Podcasts   | off     | Managing their own podcast subscriptions                        |
| Sharing    | off     | Generating public shares (see [Sharing](public-shares.md))      |
| Jukebox    | off     | Controlling jukebox-mode playback (see [Jukebox](./jukebox.md)) |
| Scrobbling | on      | Keeping track of what they listen on BeetstreamNext             |

A new user account starts with the defaults above unless overridden at creation.

> **Note:** A few roles from the spec (`coverArtRole`, `uploadRole`, `videoConversionRole`) are exposed for client/spec compatibility but do currently nothing in BeetstreamNext.

## Chat

BeetstreamNext implements the standard Subsonic chat endpoints (`getChatMessages`/`addChatMessage`). Any client with a chat feature can use it as a simple message board.

In addition, you can post server announcements from the admin panel, and edit or delete any user's chat message for moderation. See [Web UI usage](../usage/webui.md#chat-moderation).

---

See the [configuration reference](../configuration.md) for any of the settings mentioned here.
