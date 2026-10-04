# Features overview

## Zero-file-modification by default

BeetstreamNext is designed for people who manage metadata inside Beets but don't want tags written back to disk.
Every setting that would write something to your files/disk defaults to **off**, and disk writes in general are additionally gated behind the `allow_disk_writes` setting.

## OpenSubsonic API: Full coverage

BeetstreamNext implements (almost) all of the Subsonic/OpenSubsonic REST API, including its extensions.

The only endpoints it doesn't support are the video-related ones (BeetstreamNext is an audio server, and Beets doesn't manage video anyway).
See [API coverage](../api-coverage.md) for the full endpoint-by-endpoint checklist, and [Tested clients](../clients.md) for apps confirmed to work.

## BeetstreamNext's additional features

| Area                   | Additions over Beets                                                                                                        | More details                                                  |
|------------------------|-----------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------|
| Streaming              | Direct play, on-the-fly transcoding, adaptive-bitrate HLS, server-side ReplayGain, self-healing streams                     | [Streaming & audio quality](./streaming.md)                   |
| Metadata & artwork     | Artist bios, top tracks, similar artists/songs, album/artist ratings, lyrics, album editions, sonic similarity              | [Library augmentation](library-augmentation.md)               |
| Multi-user             | Multi-user auth, user roles, bookmarks/ratings/favorites/queues that sync across devices, chat features                     | [Accounts, permissions & chat](./accounts-and-permissions.md) |
| Non-library audio      | Internet radio stations, podcast subscriptions & downloads                                                                  | [Radio & podcasts](./radio-and-podcasts.md)                   |
| Playlists              | BeetstreamNext's own per-user playlists, and read/write access to Beets' `playlist`/`smartplaylist` files                   | [Playlists](./playlists.md)                                   |
| Sharing                | Public landing pages and download links (no client or account required)                                                     | [Sharing](public-shares.md)                                   |
| Security               | IP allow/deny lists, adaptive rate-limiting                                                                                 | [Security](security.md)                                       |
| Jukebox mode           | OpenSubsonic's jukebox mode, augmented by Sonos and Chromecast support                                                      | [Jukebox mode](./jukebox.md)                                  |
| Beets import           | Betanin-like interactive or non-interactive imports (also triggerable from Subsonic clients) and watched-folder auto-import | [Beets import](./beets-import.md)                             |
| Advanced Beets queries | Prefix any search from your client app with `beets:` or `b:` to run a complex Beets query instead of simple search          | [Library augmentation](library-augmentation.md)               |

## External data sources

Additional data and library augmentation comes from third-party services (they are all entirely optional):

| Data                                               | Source                                                                                                      | Enabled by                                |
|----------------------------------------------------|-------------------------------------------------------------------------------------------------------------|-------------------------------------------|
| Artist biography                                   | [Last.fm](https://www.last.fm/api) (if `lastfm_api_key` is set), or [Wikipedia](https://www.wikipedia.org/) | `fetch_artists_biographies`               |
| Artist top tracks & similar artists/songs          | [Last.fm](https://www.last.fm/api)                                                                          | `lastfm_api_key`                          |
| Artist images                                      | [Deezer](https://developers.deezer.com/api)'s public search API                                             | `fetch_artists_images`                    |
| Missing album art                                  | [Cover Art Archive](https://coverartarchive.org/)                                                           | `fetch_artists_images`/`save_album_art`   |
| Album/artist community rating                      | [Discogs](https://www.discogs.com/developers)' public rating                                                | `discogs_ratings`                         |
| Album edition/version info (e.g. "Deluxe Edition") | [MusicBrainz](https://musicbrainz.org/)                                                                     | `fetch_album_version`                     |
| Acoustic similarity & playlist path-finding        | [AudioMuse-AI](https://github.com/NeptuneHub/AudioMuse-AI) (your own self-hosted instance)                  | `audiomuse_url` and `audiomuse_api_token` |
| Radio station / podcast favicons                   | The station/feed's site, falling back to DuckDuckGo's icon proxy                                            | automatic, when an icon isn't supplied    |
| Internet radio station discovery                   | [Radio Browser](https://www.radio-browser.info/)                                                            | `enable_radio_discovery`                  |
| Podcast channel discovery                          | [Podcast Index](https://podcastindex.org/)                                                                  | `enable_podcast_discovery`                |
| Song Lyrics                                        | Uses Beets' `lyrics` plugin's configured external sources                                                   | `fetch_lyrics`/`save_lyrics`              |

> [!IMPORTANT]
> None of these ever get written into Beets or your files, unless the matching `save_*` setting is also enabled. See [Library augmentation](library-augmentation.md) for more detail.

---

See the [configuration reference](../configuration.md) for any of the settings mentioned here.