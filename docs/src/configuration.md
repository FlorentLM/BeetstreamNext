# Configuration reference

BeetstreamNext settings can be set from various ways:

1. Explicit sources: 
   - via CLI flag
   - via an environment variable
   - in **plugin mode**: via **Beets' `config.yaml`**, under a `beetstreamnext:` block (see [Installation](./installation.md#plugin-mode))
   - in **standalone mode**: via **BeetstreamNext's own config YAML (see [Installation](./installation.md#standalone-mode))
2. **The Admin WebUI**: Only for settings that aren't already pinned by one of the _explicit_ sources above. When a setting is pinned by any of those sources, its field in the Admin panel shows as locked/disabled.

Settings resolve order (first one set takes precedence):

- in **plugin mode**:

        CLI flag > environment variable > Beets' config's `beetstreamnext:` block > WebUI setting > Built-in default

- in **standalone mode**:

        CLI flag > environment variable > BeetstreamNext's YAML config file > Beets' config file (if you point it at one, and only for `library-db`/`music-root`) > WebUI setting > Built-in default

List-based settings (like for example `ip_whitelist`/`ip_blacklist`) are a bit more flexible: only the individual entries coming from an _explicit_ source are pinned, but you can still add/remove other entries on top of them from the Admin panel.

Settings marked <span style="color:#fab915">**requires restart**</span> only take effect after the server is restarted. Settings marked <span style="color:#6b5c58">**standalone only**</span> are not used when running as a Beets plugin.

## Server & network

### `admin_hostname`

If set, the admin panel will only be accessible when visited via this hostname (e.g. <code>beetstreamnext.internal.example.com</code>).<br>Loopback is always allowed.

*type:* `str`

*default:* *(empty)*

*env:* `BSN_ADMIN_HOSTNAME`

---

### `external_hostname`

Your external, public hostname (e.g. <code>music.example.com</code>).

*type:* `str`

*default:* *(empty)*

*env:* `BSN_EXTERNAL_HOSTNAME`

---

### `host`

Host(s) to listen on (comma-separated for multiple).

<span style="color:#fab915">**requires restart**</span>

*type:* `list[str]`

*default:* `0.0.0.0`

*env:* `BSN_HOST`

---

### `port`

Port to listen on.

<span style="color:#fab915">**requires restart**</span>

*type:* `int`

*default:* `8080`

*env:* `BSN_PORT`

---

### `threads`

Worker threads for serving requests.

<span style="color:#fab915">**requires restart**</span>

*type:* `int`

*default:* `16`

*env:* `BSN_THREADS`

---

### `channel_timeout`

Seconds of inactivity allowed on a connection before Waitress closes it. Lower this on low-resource environments to free up connections faster.

<span style="color:#fab915">**requires restart**</span>

*type:* `int`

*default:* `120`

---

### `connection_limit`

Maximum number of simultaneous connections Waitress will accept. Lower this on low-resource environments to cap memory/socket usage.

<span style="color:#fab915">**requires restart**</span>

*type:* `int`

*default:* `100`

---

### `cors_origins`

Allowed CORS origins (comma-separated, <code>*</code> for all). Empty to disable CORS.

<span style="color:#fab915">**requires restart**</span>

*type:* `str`

*default:* *(empty)*

*env:* `BSN_CORS_ORIGINS`

---

### `cors_supports_credentials`

Allow CORS requests with credentials (cookies, HTTP auth).

<span style="color:#fab915">**requires restart**</span>

*type:* `bool`

*default:* `False`

*env:* `BSN_CORS_CREDENTIALS`

---

### `reverse_proxy`

Server is behind a reverse proxy (Nginx, Caddy, Traefik, etc.).

<span style="color:#fab915">**requires restart**</span>

*type:* `bool`

*default:* `False`

*env:* `BSN_REVERSE_PROXY`

---

### `proxy_hops`

Number of trusted reverse proxies in front of the server. Only used if <code>reverse_proxy</code> is enabled

<span style="color:#fab915">**requires restart**</span>

*type:* `int`

*default:* `1`

*env:* `BSN_PROXY_HOPS`

---

### `sendfile_method`

Offload direct (non-transcoded) file serving to the reverse proxy instead of streaming bytes through Python. Use <code>x-accel-redirect</code> for Nginx, <code>x-sendfile</code> for Apache.<br>Only takes effect when <code>reverse_proxy</code> is enabled and the proxy is configured to honor the header.

*type:* `str`

*default:* `off`, *choices:* `off`, `x-accel-redirect`, `x-sendfile`

---

### `sendfile_internal_prefix`

Internal URI prefix your Nginx config maps, via an internal-only <code>location</code> block, to the music root directory.<br>Only used when <code>sendfile_method</code> is <code>x-accel-redirect</code>.

*type:* `str`

*default:* `/_bsn_internal`

---

### `trusted_hosts`

Allowed Host headers (domain names/IPs, comma-separated).<br>If empty, all hosts are allowed.<br>Loopback is always allowed.

*type:* `str`

*default:* *(empty)*

*env:* `BSN_TRUSTED_HOSTS`

---

### `legacy_auth`

Allow legacy MD5 token / cleartext password authentication. API-key authentication always works.

*type:* `bool`

*default:* `False`

*env:* `BSN_LEGACY_AUTH`

---

### `public_now_playing`

Show the currently playing song on the public home page.

*type:* `bool`

*default:* `False`

---

### `homepage_github_link`

Show the "View on GitHub" link on the public home page.

*type:* `bool`

*default:* `True`

---

### `homepage_docs_link`

Show the "Read the docs" link on the public home page.

*type:* `bool`

*default:* `True`

---

### `homepage_connect_hint`

Show the "Connect your favourite client using this URL" hint and server URL on the public home page.

*type:* `bool`

*default:* `True`

---

### `music_root`

Root directory your beets library's file paths are relative to.

<span style="color:#6b5c58">**standalone only**</span>, <span style="color:#fab915">**requires restart**</span>

*type:* `str`

*default:* *(empty)*

---

### `library_path`

Path to the beets library.db to serve.

<span style="color:#6b5c58">**standalone only**</span>, <span style="color:#fab915">**requires restart**</span>

*type:* `str`

*default:* *(empty)*

---

### `library_remote_path`

Optional, mount point used by another container that accesses the beets library (e.g. Betanin), if it differs from where 'music_root' mounts it in the BeetstreamNext container. Leave empty if both containers mount the music volume at the same path.

<span style="color:#6b5c58">**standalone only**</span>

*type:* `str`

*default:* *(empty)*

---

### `playlist_dir`

Directory for BeetstreamNext's own playlists. Leave empty to disable.

<span style="color:#fab915">**requires restart**</span>

*type:* `str`

*default:* *(empty)*

*env:* `BSN_PLAYLIST_DIR`

---

### `strict_beets_version_check`

Refuse to start on beets version mismatch (see Server Info) instead of just warning.

<span style="color:#6b5c58">**standalone only**</span>, <span style="color:#fab915">**requires restart**</span>

*type:* `bool`

*default:* `False`

## Library & metadata

### `allow_disk_writes`

Required to allow <code>beet import</code> scans to modify content on disk (writing tags in the files or copying/moving files). Not required if the loaded Beets config has <code>write</code>/<code>copy</code>/<code>move</code> all disabled.

*type:* `bool`

*default:* `False`

*env:* `BSN_ALLOW_DISK_WRITES`

---

### `never_transcode`

Never transcode files, always stream the original.

*type:* `bool`

*default:* `False`

*env:* `BSN_NEVER_TRANSCODE`

---

### `lastfm_api_key`

Last.fm API key for fetching metadata.

<span style="color:#6b5c58">**sensitive**</span>

*type:* `str`

*default:* *(empty)*

*env:* `BSN_LASTFM_API_KEY`

---

### `fetch_artists_images`

Fetch missing artist images from external services.

*type:* `bool`

*default:* `False`

*env:* `BSN_FETCH_ARTISTS_IMAGES`

---

### `save_artists_images`

Save fetched artist images to disk.

*type:* `bool`

*default:* `False`

*env:* `BSN_SAVE_ARTISTS_IMAGES`

---

### `fetch_artists_biographies`

Fetch artist short biography from Wikipedia.

*type:* `bool`

*default:* `False`

---

### `save_album_art`

Save fetched album art on disk, alongside music files.

*type:* `bool`

*default:* `False`

*env:* `BSN_SAVE_ALBUM_ART`

---

### `follow_playlist_embedded_urls`

Fetch external images from foreign playlists' <code>#EXTALBUMARTURL</code> lines (for albums that have no art locally). Only enable this if you trust the source of your imported playlists.

*type:* `bool`

*default:* `False`

---

### `fetch_lyrics`

Fetch missing song lyrics using Beets' Lyrics plugin.

*type:* `bool`

*default:* `False`

---

### `save_lyrics`

Save fetched lyrics to the beets library database.

*type:* `bool`

*default:* `False`

---

### `fetch_album_version`

Fetch album version info ("Deluxe Edition", "Japanese Expanded Edition", etc.) from MusicBrainz.

*type:* `bool`

*default:* `False`

---

### `save_album_version`

Save fetched album version info to the beets database.

*type:* `bool`

*default:* `False`

---

### `discogs_ratings`

Use Discogs' public community rating for an album's average rating.<br><br><code>fallback</code>: Only use Discogs when nobody on this server has rated the album locally. It never overrides a local rating.<br><br><code>prefer</code>: Always uses Discogs when available (falling back to the local average when it isn't).

*type:* `str`

*default:* `off`, *choices:* `off`, `fallback`, `prefer`

---

### `ignored_articles`

Space-separated articles (across any language) to ignore when sorting artists alphabetically (for instance, "The Beatles" -> B).

*type:* `str`

*default:* `The A An Der Die Das Ein Eine El La Los Las Un Una Le Les Il Lo Gli Uno O Os As Um Uma De Het Den Det`

---

### `ratings_writeback_user`

Commit this user's Likes and Ratings into the Beets library so they survive outside BeetstreamNext. Beets has no concept of per-user data, so only one user's changes can be committed this way.<br>Leave unset to disable.

*type:* `str`

*default:* *(empty)*

---

### `external_playlists_editors`

Who can rename/edit/delete non-BeetstreamNext playlists (from Beets' <code>playlist</code> plugin directory). <br><br><code>smartplaylist</code>-generated playlists are always read-only.

*type:* `list[str]`

*default:* *(empty)*

---

### `enable_radio_discovery`

Enable Radio Browser API for station discovery.

*type:* `bool`

*default:* `False`

---

### `fetch_radio_images`

Automatically fetch station icon when adding a new radio station.

*type:* `bool`

*default:* `True`

---

### `audiomuse_api_token`

API token for your AudioMuse-AI instance.

<span style="color:#6b5c58">**sensitive**</span>

*type:* `str`

*default:* *(empty)*

*env:* `BSN_AUDIOMUSE_API_TOKEN`

---

### `audiomuse_url`

URL to your AudioMuse-AI instance (e.g. <code>http://localhost:8000</code>) to enable sonic similarity endpoints.

*type:* `str`

*default:* *(empty)*

## Podcasts

### `podcast_storage_dir`

Directory to store downloaded podcast episode audio. Leave empty to use the default cache location.

*type:* `str`

*default:* *(empty)*

---

### `podcast_auto_download_count`

Number of episodes to download of a channel's most recent episodes when added. Set to <code>0</code> to disable and only download episodes on request.

*type:* `int`

*default:* `3`

---

### `enable_podcast_discovery`

Enable Podcast Index API for channel discovery.

*type:* `bool`

*default:* `False`

---

### `podcastindex_api_key`

API key for the <a href="https://podcastindex.org/" target="_blank" rel="noopener">Podcast Index</a>, used for podcast channel discovery.

<span style="color:#6b5c58">**sensitive**</span>

*type:* `str`

*default:* *(empty)*

*env:* `BSN_PODCASTINDEX_API_KEY`

---

### `podcastindex_api_secret`

API secret for the Podcast Index.

<span style="color:#6b5c58">**sensitive**</span>

*type:* `str`

*default:* *(empty)*

*env:* `BSN_PODCASTINDEX_API_SECRET`

## Audio & jukebox

### `replaygain_enabled`

Apply ReplayGain normalization on the server side.

*type:* `bool`

*default:* `False`

---

### `replaygain_preamp`

Additional gain (dB) to apply.

*type:* `int`

*default:* `0`

---

### `replaygain_fallback`

Gain (dB) to apply to tracks without ReplayGain tags in beets' library.

*type:* `int`

*default:* `-6`

---

### `audio_peak_limit`

Always prevent audio peaks from exceeding 0 dB (prevent clipping).

*type:* `bool`

*default:* `False`

---

### `ffmpeg_path`

Path to the ffmpeg binary, if it isn't on the system PATH (e.g. <code>/usr/local/bin/ffmpeg</code>). <br>Leave empty to auto-detect from PATH.

*type:* `str`

*default:* *(empty)*

---

### `jukebox_allowed`

Allow jukebox mode: the server can play audio on its own hardware, or on a Sonos/Chromecast compatible speaker (client apps act as remote controls).

*type:* `bool`

*default:* `False`

---

### `jukebox_backend`

Defines where jukebox mode will play audio from.<br><code>server_hardware</code> plays on this server's own audio hardware using mpv.<br><code>sonos</code> or <code>chromecast</code> stream to a speaker on the local network.

*type:* `str`

*default:* `server_hardware`, *choices:* `server_hardware`, `sonos`, `chromecast`

---

### `jukebox_hardware_device`

Which hardware jukebox mode plays audio on.<br>For <code>server_hardware</code>, this is the audio output device as mpv's <code>--audio-device</code> expects (e.g. <code>alsa/hw:0,0</code> or <code>coreaudio/BuiltInSpeakerDevice</code>), or empty to use the system default.<br>For <code>sonos</code>, the speaker's IP address.<br>For <code>chromecast</code>, the device's UUID.

*type:* `str`

*default:* *(empty)*

---

### `mpv_path`

For <code>server_hardware</code> backend only. Path to the mpv binary, if it isn't on the system PATH (e.g. <code>/usr/local/bin/mpv</code>).<br>Leave empty to auto-detect from PATH.

*type:* `str`

*default:* *(empty)*

## Security

### `ip_whitelist`

Allowed IPs (empty = allow all except blacklist).

*type:* `list[str]`

*default:* *(empty)*

*env:* `BSN_IP_WHITELIST`

---

### `ip_blacklist`

Banned IPs.

*type:* `list[str]`

*default:* *(empty)*

*env:* `BSN_IP_BLACKLIST`

---

### `rate_limit_max_failures`

Failed attempts before an IP is rate-limited.

*type:* `int`

*default:* `5`

---

### `rate_limit_block_window`

Seconds before failures roll off.

*type:* `int`

*default:* `300`

---

### `rate_limit_ip_max_failures`

Failed attempts from a single IP (across any usernames tried) before that IP is blocked outright. Catches attackers rotating usernames to dodge the per-user limit above.

*type:* `int`

*default:* `20`

---

### `rate_limit_ip_block_window`

Seconds before an IP-wide failure count rolls off.

*type:* `int`

*default:* `3600`
