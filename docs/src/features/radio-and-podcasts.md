# Radio & podcasts

Internet radios and Podcasts do not come from your Beets library, and are managed entirely by **BeetstreamNext**.

## Internet radio

**BeetstreamNext** implements the Subsonic internet radio endpoints (`getInternetRadioStations`, `createInternetRadioStation`, `updateInternetRadioStation`, `deleteInternetRadioStation`).

Each user has their own radio station list from their [account page](./accounts-and-permissions.md#user-account-page) or any Subsonic client that supports them. No specific role is required.

> **Note:** When upgrading _from_ **BeetstreamNext** v2.0.7 (where stations were shared server-wide), existing stations are automatically copied to every existing user.

- **Station discovery**: Set `enable_radio_discovery` to search **[Radio Browser](https://www.radio-browser.info/)**, a community-maintained directory of internet radio streams.
- **Icons**: BeetstreamNext tries to scrape icons from the station homepage's `<link rel="icon">`/`apple-touch-icon` tags, or falls back to [**DuckDuckGo**'s icon proxy](https://duckduckgo.com/duckduckgo-help-pages/privacy/favicons).

## Podcasts

**BeetstreamNext** implements the full Subsonic podcast feature set: subscribing to RSS feeds, browsing channels and episodes, and downloading episodes for offline playback in a client.

- **Subscriptions per-user**: Each user with the `podcastRole` can manage their own subscriptions.
- **Channel discovery**: Set `enable_podcast_discovery` and configure `podcastindex_api_key`/`podcastindex_api_secret` (a free account at [podcastindex.org](https://podcastindex.org/)) to search for podcasts from the admin panel.
- **Storage and auto-download**:
  - `podcast_storage_dir`: where downloaded episodes are stored. Leave empty to use the default location.
  - `podcast_auto_download_count`: how many of a channel's most recent episodes are automatically downloaded when a new channel is added. Set it to `0` to disable auto-download and only fetch episodes on request (see **Note** below).
- **OPML Import/Export**: Supports importing/exporting existing podcast subscriptions.

> **Note:** Many clients do not expose any "Download" button for individual episodes. Because of this, **BeetstreamNext** triggers a download automatically when it receives an episode streaming request: the audio is written to disk _and_ forwarded to the client at the same time.

---

See the [configuration reference](../configuration.md#podcasts) and [`enable_radio_discovery`](../configuration.md#library--metadata) for these settings.
