# Privacy

**BeetstreamNext** collects **_nothing_** about you. There are no ads, no telemetry, no analytics, no crash reporting and no update check.

> tl;dr: I never see your data. I don't care about it.

## What is stored on your server

Everything stays in your Beets library and BeetstreamNext's own database, on your machine:

- User accounts (usernames and password hashes, or the credentials needed for Subsonic authentication),

- Ratings, likes, playlists, bookmarks, play queues, play counts and chat messages

- Web UI sessions, including the IP address and user agent they were opened from

- The server log, which can contain IP addresses and requested URLs

_Nothing_ is sent anywhere unless you enable one of the features below.

## Outgoing requests

Some features contact third-party services **from your server** (never from the browser). These requests carry names of artists, albums or feeds from your library, and your server's IP address. They never include account or listening data.

| Feature                     | Service                                                                                                                  | What is sent                                   |
|-----------------------------|--------------------------------------------------------------------------------------------------------------------------|------------------------------------------------|
| Metadata and album versions | [MusicBrainz](https://musicbrainz.org/), [Cover Art Archive](https://coverartarchive.org/)                               | Release / artist identifiers and names         |
| Artist and album info       | [Last.fm](https://www.last.fm/)                                                                                          | Artist / album names and your `lastfm_api_key` |
| Album ratings               | [Discogs](https://www.discogs.com/)                                                                                      | Release identifiers                            |
| Artist images               | [Deezer](https://www.deezer.com/)                                                                                        | Artist / album names                           |
| Radio stations              | [Radio Browser](https://www.radio-browser.info/), the station's own website, [DuckDuckGo icons](https://duckduckgo.com/) | Station names and domains (for favicons)       |
| Podcasts                    | The feed and media hosts you subscribe to, and [Podcast Index](https://podcastindex.org/) for search                     | Feed URLs and search terms                     |
| Notifications               | Whatever [Apprise](https://appriseit.com/services/) service you configure                                                | The notification content                       |
| Sonic similarity            | Your own [AudioMuse-AI](https://github.com/NeptuneHub/AudioMuse-AI) instance                                             | Track identifiers                              |

Each service has its own privacy policy. Features you don't use don't make any requests, and the corresponding settings can be turned off in the [configuration](./configuration.md).

## The web UI

The web UI loads everything (scripts, styles and fonts) from your own server. It doesn't use third-party CDNs, trackers or cookies other than the session cookie needed to stay logged in.

## This documentation site and the repository

The documentation is a static site hosted on GitHub Pages. It has no analytics and no cookies of its own, and loads no third-party scripts or fonts.

## Questions

If something here is unclear or wrong, please [open an issue](https://github.com/FlorentLM/BeetstreamNext/issues).
