<br />

<div align="center">

  <a href="https://github.com/FlorentLM/BeetstreamNext">
    <img src="beetsplug/beetstreamnext/static/images/logo.svg" alt="Logo" width="128" height="128">
  </a>

<h3 align="center">BeetstreamNext</h3>
  <p>
  A fully-featured OpenSubsonic API server for Beets.io music libraries.
  <br/>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)  

  </p>
</div>

BeetstreamNext exposes your [Beets](https://beets.io) music library over the [OpenSubsonic API](https://opensubsonic.netlify.app/), allowing you to stream your music to any Subsonic client. 

## Screenshots

<p float="left">
    <img src="beetsplug/beetstreamnext/static/images/home_screenshot.png" alt="home page screenshot" width="300">
    <img src="beetsplug/beetstreamnext/static/images/admin_screenshot.png" alt="admin page screenshot" width="300">
</p>

---

## API coverage & Features

BeetstreamNext implements the vast majority of the OpenSubsonic API specification, **excluding video streaming and podcast management**.

It also introduces several structural enhancements and features.

*   **Authentication:** Supports modern API key authentication and legacy MD5 token authentication for older clients.
*   **Multi-user system:** Individual bookmarks, ratings, favorites, play statistics, and play queues (allowing you to save and restore your active queue across devices).
*   **Metadata integration:** Retrieves artist biographies, top tracks, and similar artists from Last.fm or Wikipedia.
*   **Album artworks / Artists images**: Grabs and serves the local album art path from your Beets library, or fetches and saves the images from [Cover Art Archive](https://coverartarchive.org/) and Deezer.
*   **Advanced Beets queries (search hook):** You can execute complex Beets queries (e.g. regex, field-specific queries, fuzzy matching) directly inside your Subsonic client's search bar. Simply prefix your query with `beets:` or `b:` (e.g. `beets:length:..3:30` to find tracks shorter than 3:30).
*   **Zero-file-modification architecture:** Designed for users who manage metadata inside Beets but do *not* want to modify or write metadata tags directly to their media files, for archival purposes. 
*   **Lyrics retrieval:** Serves internal Beets lyrics or fetches them on-the-fly using the Beets `lyrics` plugin.
*   **On-the-fly transcoding:** Serves raw files directly or transcodes lossy/lossless targets on-the-fly using FFmpeg.
*   **HTTP Live Streaming (HLS):** AAC-encoded dynamic HLS streaming with full Adaptive Bitrate (ABR) support for clients that request multi-bitrate variant playlists.
*   **Public shares:** Generates public landing pages for shared files with a secure download endpoint.
*   **Access controls:** Built-in IP whitelisting, blacklisting, and adaptive login rate-limiting (monitored and cleared via the admin panel).
*   **Admin WebUI:** Settings can be changed via a rather simple but useful WebUI.

[//]: # (### Coming soon:)

[//]: # ()
[//]: # (*   **Acoustic similarity engine:** Native integration with [AudioMuse-AI]&#40;https://github.com/FlorentLM/BeetstreamNext&#41; to support OpenSubsonic's `sonicSimilarity` extension.)

---

## Installation & Deployment

1.  **Clone and Install:**
    ```bash
    git clone https://github.com/FlorentLM/BeetstreamNext.git
    cd BeetstreamNext
    pip install .
    ```
2.  **Enable in Beets' `config.yaml`:**
    ```yaml
    plugins: beetstreamnext
    ```
3.  **Create your admin user:**
    ```bash
    beet beetstreamnext --create-user
    ```
4.  **Run:**
    ```bash
    beet beetstreamnext
    ```

---

## Configuration

Settings can be managed initially via Beets' `config.yaml`, and subsequently adjusted directly inside the Admin WebUI (which takes precedence).

```yaml
beetstreamnext:
  host: 0.0.0.0
  port: 8080
  reverse_proxy: false          # Enable if running behind Nginx/Caddy
  
  # Network & Access restrictions
  admin_hostname: ''            # Restrict admin panel to this host (e.g., admin.local)
  external_hostname: ''         # Force public shares to generate with this domain name
  ip_whitelist: ''              # List of IPs (space or comma-separated) to allow
  ip_blacklist: ''              # List of IPs (space or comma-separated) to block
  cors: ''                      # Allowed CORS origins for web-based clients
  
  # Library options
  enable_public_now_playing: false # Toggle the public homepage widget
  fetch_artists_images: true    # Fetch artist photos from Deezer
  save_artists_images: true     # Save fetched artist photos to music folders
  save_album_art: true          # Save fetched album art to music folders
```

### Environment variables
*   `BEETSTREAMNEXT_KEY`: Secret key used to encrypt legacy passwords at rest.
*   `LASTFM_API_KEY`: (Optional) To enable biographies, top tracks, and similar artist queries.

## Using behind a reverse proxy

BeetstreamNext uses modern standard HTTP headers to know the original client's IP, 
so the configuration should be pretty straightforward.

**Nginx** for instance would look like this:
```
location /beetstreamnext {
    proxy_pass http://127.0.0.1:8080;
    
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    
    # If hosting in a subfolder, tell BeetstreamNext what the subfolder is!
    proxy_set_header X-Forwarded-Prefix /beetstreamnext;
}
```

**Caddy** (v2) passes all the required headers by default, so it's just:
```
example.com {
    reverse_proxy 127.0.0.1:8080
}
```

### Web Clients and CORS

By default, BeetstreamNext is configured with CORS (Cross-Origin Resource Sharing) disabled. 
If you use native mobile or desktop apps, you probably do not need to change anything (native apps ignore CORS and will work out of the box).

If you want to use a _web-based_ Subsonic player hosted on a different domain, 
you must allow the web player's URL in your Beets config, otherwise your web browser will block the connection for security reasons.

```yaml
beetstreamnext:
    cors: 'https://music.example.com' # also accepts a comma-separated list or a wildcard '*'
```

If you are using a SSO gateway (Authelia, Authentik, etc.), or if the web-based player is a bit quirky, you might also
need to enable this:

```yaml
beetstreamnext:
    cors_supports_credentials: true
```

**Warning:** DO NOT set `cors: '*'` alongside `cors_supports_credentials: yes`. 
Doing so could allow *any* malicious website you visit to silently interact with your BeetstreamNext server in the background.

---

## Tested clients

BeetstreamNext should be compatible with virtually any Subsonic/OpenSubsonic client.
I tested it and confirmed it working with:

#### Android
- [Agin Music](https://github.com/aginrocks/agin-music-mobile)
- [Amcfy Music](https://www.amcfy.com/)
- [Symfonium](https://symfonium.app/)
- [Tempo](https://github.com/CappielloAntonio/tempo)
- [Tempus](https://github.com/eddyizm/tempus)
- [SubTune](https://github.com/TaylorKunZhang/SubTune)
- [GoSonic](https://play.google.com/store/apps/details?id=com.readysteadygosoftware.gosonic)
- [K-19 Player](https://github.com/ulysg/k19-player)
- [Ultrasonic](https://gitlab.com/ultrasonic/ultrasonic)
- [Subtracks](https://github.com/austinried/subtracks)

#### iOS/iPadOS
- [Amperfy](https://github.com/BLeeEZ/amperfy)
- [Submariner](https://github.com/SubmarinerApp/Submariner)
- [Supersonic](https://github.com/dweymouth/supersonic)

#### Desktop
- [Feishin](https://github.com/jeffvli/feishin)
- [Aonsoku](https://github.com/victoralvesf/aonsoku)

---

## Missing endpoints

See [here](OpenSubsonic_endpoints.md)

---

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.