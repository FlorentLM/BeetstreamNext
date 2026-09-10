# Radio & podcasts

Internet radios and Podcasts do not come from your Beets library, these are managed entirely by BeetstreamNext.

## Internet radio

BeetstreamNext implements the Subsonic internet radio endpoints (`getInternetRadioStations`, `createInternetRadioStation`, `updateInternetRadioStation`, `deleteInternetRadioStation`).

Radio stations can be managed from the admin panel or any client that supports them.

- **Station discovery (external)**: Set `enable_radio_discovery` to search [Radio Browser](https://www.radio-browser.info/), a community-maintained directory of internet radio streams.
- **Icons**: BeetstreamNext tries to scrape icons from the station homepage's `<link rel="icon">`/`apple-touch-icon` tags, or falls back to [DuckDuckGo's icon proxy](https://duckduckgo.com/duckduckgo-help-pages/privacy/favicons). **(external)**

## Podcasts

BeetstreamNext implements the full Subsonic podcast feature set: subscribing to RSS feeds, browsing channels and episodes, and downloading episodes for offline playback in a client.

- **Subscriptions are per-user**: Each user with the `podcastRole` (disabled by default) manages their own subscriptions.
- **Storage and auto-download**:
  - `podcast_storage_dir` controls where downloaded episode audio is stored. Leave it empty to use the default cache location.
  - `podcast_auto_download_count` controls how many of a channel's most recent episodes are automatically downloaded when a new channel is added. Set it to `0` to disable auto-download and only fetch episodes on request (see **Note** below).

> **Note:** Many clients simply do not expose any "Download" button for individual episodes. Because of this, BeetstreamNext triggers a download automatically when it receives an episode streaming request: the audio is written to disk _and_ forwarded to the client at the same time.

---

See the [configuration reference](../configuration.md#podcasts) and [`enable_radio_discovery`](../configuration.md#library--metadata) for these settings.
