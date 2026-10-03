# Library augmentation

A lot of what shows up in your Subsonic client (artist photos, biographies, community ratings, “songs like this one”, etc) isn’t in your Beets library: **BeetstreamNext** collects and serves additional data.

## Metadata enrichment

- **Artist biographies**: From [Last.fm](https://www.last.fm/api) if `lastfm_api_key` is set, otherwise from [Wikipedia](https://www.wikipedia.org/) if `fetch_artists_biographies` is enabled.
- **Artist top tracks and similar artists/songs**: From Last.fm, using `lastfm_api_key`.
- **Album/artist ratings**: Either your local users' ratings, or [Discogs](https://www.discogs.com/developers)' public community rating via `discogs_ratings` (`off` / `fallback` / `prefer`).
- **Lyrics**: Served from Beets' stored lyrics if present, or fetched on-the-fly using Beets' `lyrics` plugin (`fetch_lyrics`), optionally written back into Beets with `save_lyrics`.
- **Album version/edition info** (e.g. "Deluxe Edition", "Japanese Expanded Edition"): From [MusicBrainz](https://musicbrainz.org/), via `fetch_album_version`, optionally saved with `save_album_version`.

## Artwork

- **Album art / artist images** are served from your Beets library's local art path when available.
- When missing:
  - Album art from [Cover Art Archive](https://coverartarchive.org/).
  - Artist images from [Deezer](https://developers.deezer.com/api)'s public search API.
  - Controlled by `fetch_artists_images`.
  - `save_artists_images`/`save_album_art` optionally persist what was fetched to disk.
  - `follow_playlist_embedded_urls` lets cover art be pulled from a foreign playlist's `#EXTALBUMARTURL` lines, for albums that have no local art. (_only enable for playlist sources you trust!)_

## Sonic similarity

To expose OpenSubsonic's `sonicSimilarity` extension, BeetstreamNext integrates with **[AudioMuse-AI](https://github.com/NeptuneHub/AudioMuse-AI)**, a self-hosted audio-analysis service. This is used for *acoustic* similarity (based on the audio, not tags or genre), to find similar songs, and path-finding a playlist between two tracks based on their audio embeddings.

Point `audiomuse_url` at your **AudioMuse-AI** instance and set `audiomuse_api_token` to enable it. The admin panel can trigger a full-library fingerprinting run on AudioMuse-AI directly.

## Advanced search: Beets query passthrough

Any search request can be turned into a raw Beets query by prefixing it with  `beets:` or `b:`, for example `b:length:..3:30` to find tracks under 3:30. This runs Beets' own complex query DSL (regex, field-specific, fuzzy matching) straight from your client's search bar, instead of a normal search.

---

See the [configuration reference](../configuration.md) for any of the settings mentioned here.
