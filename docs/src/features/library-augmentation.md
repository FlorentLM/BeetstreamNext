# Library augmentation

Beets stores what's tagged in your files (artist, album, MusicBrainz IDs, and whatever else your import config pulled in). BeetstreamNext can add a fair amount of extra, live-fetched information on top of that, none of it required, and none of it written anywhere (unless you _explicitly_ want to).

> Every data point on this page marked **(external)** comes from a third-party service, not from your Beets library. See [Features overview](./index.md#external-data-sources-what-doesnt-come-from-beets) for the full list.

## Metadata enrichment

- **Artist biographies**: From [Last.fm](https://www.last.fm/api) if `lastfm_api_key` is set, otherwise from [Wikipedia](https://www.wikipedia.org/) if `fetch_artists_biographies` is enabled. **(external)**
- **Artist top tracks and similar artists/songs**: From Last.fm, using `lastfm_api_key`. **(external)**
- **Album/artist ratings**: Either your users' own local ratings, or [Discogs](https://www.discogs.com/developers)' public community rating via `discogs_ratings` (`off` / `fallback` / `prefer`). **(external, opt-in)**
- **Lyrics**: Served from Beets' own stored lyrics if present, or fetched on-the-fly using Beets' `lyrics` plugin (`fetch_lyrics`), optionally written back into Beets with `save_lyrics`.
- **Album version/edition info** (e.g. "Deluxe Edition", "Japanese Expanded Edition"): From [MusicBrainz](https://musicbrainz.org/), via `fetch_album_version`, optionally saved with `save_album_version`. **(external)**

## Artwork

- **Album art / artist images** are served from your Beets library's local art path when available: nothing external needed here.
- When missing, BeetstreamNext can fetch:
  - Album art from [Cover Art Archive](https://coverartarchive.org/). **(external)**
  - Artist images from [Deezer](https://developers.deezer.com/api)'s public search API. **(external)**
  - Controlled by `fetch_artists_images`. `save_artists_images`/`save_album_art` optionally persist what was fetched to disk.
- `follow_playlist_embedded_urls` additionally lets it pull cover art from a foreign playlist's `#EXTALBUMARTURL` lines, for albums that have no local art. **(external)** - _only enable for playlist sources you trust!_

## Sonic similarity

To expose OpenSubsonic's `sonicSimilarity` extension, BeetstreamNext integrates with [AudioMuse-AI](https://github.com/NeptuneHub/AudioMuse-AI): a self-hosted audio-analysis service. **(external)**

For *acoustic* similarity (based on the audio, not tags or genre): finding similar songs, and path-finding a playlist between two tracks based on their audio embeddings.

Point `audiomuse_url` at your AudioMuse-AI instance and set `audiomuse_api_token` to enable it. The admin panel can trigger a full-library fingerprinting run on AudioMuse-AI directly.

## Advanced search: Beets query passthrough

Any search request can be turned into a raw Beets query by prefixing it with  `beets:` or `b:`, for example `b:length:..3:30` to find tracks under 3:30. This runs Beets' own complex query DSL (regex, field-specific, fuzzy matching) straight from your client's search bar, instead of a normal search.

---

See the [configuration reference](../configuration.md) for any of the settings mentioned here.
