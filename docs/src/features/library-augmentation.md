# Library augmentation

A lot of what shows up in your Subsonic client (artist photos, biographies, community ratings, “songs like this one”, etc) isn’t in your Beets library: **BeetstreamNext** collects and serves additional data.

## Metadata enrichment

- **Artist biographies**: From [Last.fm](https://www.last.fm/api) if `lastfm_api_key` is set, otherwise from [Wikipedia](https://www.wikipedia.org/) if `fetch_artists_biographies` is enabled.

- **Artist top tracks and similar artists/songs**: From Last.fm, using `lastfm_api_key`.

- **Album/artist ratings**: Either your local users' ratings, or [Discogs](https://www.discogs.com/developers)' public community rating via `discogs_ratings` (`off` / `fallback` / `prefer`).

- **Lyrics**: Served from Beets' stored lyrics if present, from `.lrc` files next to the audio files (tip: use the [filetote](https://github.com/gtronset/beets-filetote) plugin), or fetched on-the-fly using Beets' `lyrics` plugin (`fetch_lyrics`), optionally written back into Beets with `save_lyrics`.

- **Album version/edition info** (e.g. "Deluxe Edition", "Japanese Expanded Edition"): From [MusicBrainz](https://musicbrainz.org/), via `fetch_album_version`, optionally saved with `save_album_version`.

## Artwork

- **Album art / artist images** are served from your Beets library's local art path when available.

- When missing:
  - Album art from [Cover Art Archive](https://coverartarchive.org/).

  - Artist images from [Deezer](https://developers.deezer.com/api)'s public search API.

  - Controlled by `fetch_artists_images`.

  - `save_album_art` allows saving album art next to the music files.

  - `follow_playlist_embedded_urls` lets cover art be pulled from a foreign playlist's `#EXTALBUMARTURL` lines, for albums that have no local art. (_only enable for playlist sources you trust!)_

## Sonic similarity

To expose OpenSubsonic's `sonicSimilarity` extension, BeetstreamNext integrates with **[AudioMuse-AI](https://github.com/NeptuneHub/AudioMuse-AI)**, a self-hosted audio-analysis service. This is used for *acoustic* similarity (based on the audio, not tags or genre), to find similar songs, and path-finding a playlist between two tracks based on their audio embeddings.

Point `audiomuse_url` at your **AudioMuse-AI** instance and set `audiomuse_api_token` to enable it. The admin panel can trigger a full-library fingerprinting run on AudioMuse-AI directly.

## Hiding items

Songs, albums or artists you dislike or rate low can be hidden (on a per user basis). See [`hide_dislikes`](../configuration.md#hide_dislikes) and [`hide_low_ratings`](../configuration.md#hide_low_ratings). Use [`low_rating_cutoff`](../configuration.md#low_rating_cutoff) to set what counts as _low rated_.

Starred or high rated items are still shown, even if the parent album/artist is hidden. Use [`high_rating_cutoff`](../configuration.md#high_rating_cutoff) to set what counts as _high rated_

> [!NOTE]
>
> Dislikes and Hiding are not part of the Subsonic or OpenSubsonic specification.
>
> The `dislike` endpoint is advertised to clients via a **BeetstreamNext**-only `bsnDislike` extension, Hiding is advertised via the `bsnHiding` extension. Clients can pass `includeDisliked=true` to bypass dislike hiding on a request.
>
> See [GitHub discussion](https://github.com/opensubsonic/open-subsonic-api/discussions/193) about dislikes in OpenSubsonic.

> [!IMPORTANT]
> Some clients heavily cache the library, and so they don't play too nice with hidden items.
> 
> If hidden items keep showing up, resync the library or clear the client's cache.

## Advanced search: Beets query passthrough

Any search request can be turned into a raw Beets query by prefixing it with  `beets:` or `b:`, for example `b:length:..3:30` to find tracks under 3:30. This runs Beets' own complex query DSL (regex, field-specific, fuzzy matching) straight from your client's search bar, instead of a normal search.

### Stars, ratings and plays

**BeetstreamNext** adds a few tokens that work inside `b:` queries and return _per-user_ data:

| Token         | Values                                           | Example                                     |
|---------------|--------------------------------------------------|---------------------------------------------|
| `starred:`    | `1`/`0`, `true`/`false`, `yes`/`no`              | `b:starred:1 genre:jazz`                    |
| `rating:`     | a number or a range (`4`, `4..`, `..2`, `2..4`)] | `b:rating:4..`                              |
| `plays:`      | a number or a range                              | `b:plays:0 added:-1m..`                     |
| `lastplayed:` | a date or a range (using Beets' date syntax)     | `b:lastplayed:..2024`, `b:lastplayed:-1w..` |

- Tokens mix freely with other Beets fields and sorting: `b:artist:Beatles rating:..2 year+`.

- Prefix a token with `^` or `-` to negate it (for instance `b:^starred:1` means everything you haven't starred).

- These tokens _take precedence_ over Beets fields of the same name. That means every user gets their own results even if [`ratings_writeback_user`](../configuration.md#ratings_writeback_user) mirrors one user's stars and ratings into Beets.

---

See the [configuration reference](../configuration.md) for any of the settings mentioned here.
