<br />

<div align="center">

  <a href="https://github.com/FlorentLM/BeetstreamNext">
    <img src="beetsplug/beetstreamnext/images/beetstreamnext_logo.svg" alt="Logo" width="128" height="128">
  </a>

<h3 align="center">BeetstreamNext</h3>
  <p>
  A modern, feature-rich OpenSubsonic API server for your Beets.io music library.
  <br/>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)  

  </p>
</div>

BeetstreamNext is a [Beets.io](https://beets.io) plugin that exposes the [OpenSubsonic API](https://opensubsonic.netlify.app/), allowing you to stream your music library to any Subsonic-compatible client.
I started implementing new features to Beetstream but ended up rewriting a significant part of it, so I figured it'd make more sense to keep it as a distinct project.

Personally, I use Beets to manage my music library but I don't like to write metadata to the files. So with this, I can have the best of both worlds.

## Features

- **OpenSubsonic coverage**: All essential modern endpoints are covered
- **Multi-user system**: Bookmarks, individual ratings, favourites, play statistics, play queues (save/restore your queue across devices)...
- **Authentication**: Supports OpenSubsonic's modern API key authentication, and the legacy MD5 token auth for older clients
- **Transcoding**: On-the-fly transcoding (with FFmpeg). Direct play also available of course.
- **Album artworks / Artists images**: 
    - Grabs and serves the local album art path from your Beets library
    - Can extract embedded album artwork from media files
    - Can use the [Cover Art Archive](https://coverartarchive.org/) to fetch album artworks
    - Can fetch artist images from Deezer
- **More metadata!!**:
    - Can fetch artist info (like biographies, top tracks, similar artists, etc) from Last.fm
    - Fallback to Wikipedia for artists biographies if not from Last.fm
    - Serves internal Beets lyrics or fetches them on-the-fly via the Beets `lyrics` plugin
- **Complex queries**: Beets' advanced queries are supported in the search function. Use regex, fuzzy match, complex filters, etc., directly from your client!
    - Just use the `beets:` (or `b:`) prefix followed by your query: for instance`beet:length:..3:30` will return all songs shorter than 3 minutes 30.
    - See Beet's [Queries reference](https://beets.readthedocs.io/en/stable/reference/query.html) for more examples.

## Installation

Requires Python 3.9+ and an existing Beets library.

> [!NOTE]
> BeetstreamNext is not yet available on PyPI. Installation currently requires cloning the source code from GitHub.

1.  **Install Beets**: If you haven't already, [install and configure Beets](https://beets.readthedocs.io/en/stable/guides/main.html). You will also need `git` installed on your system.

2. **Install the Plugin**:
   ```bash
   git clone https://github.com/FlorentLM/BeetstreamNext.git
   cd BeetstreamNext
   pip install .
   ```
3. **Enable in Beets `config.yaml`**:
   ```yaml
   plugins: beetstreamnext
   ```
4. **Create a user**:
   ```bash
   beet beetstreamnext --user
   ```
   *Follow the prompts to create your admin account and receive your API Key.*

5. **Run the Server**:
   ```bash
   beet beetstreamnext
   ```

## Configuration

Available options in your Beets `config.yaml`:

```yaml
beetstreamnext:
  host: 0.0.0.0
  port: 8080
  cors: '*'                     # Allow specific origins
  reverse_proxy: False          # Enable if running behind Nginx/Caddy

  legacy_auth: True             # Allow old MD5-based password auth (not recommended)
  never_transcode: False        # Force direct stream only (never re-encode files, even if a client requests it)
  
  # Artist images
  fetch_artists_images: True    # Fetch artist photos from Deezer when a client requests them
  save_artists_images: True     # Save fetched artist photos to their respective folders
  
  # Playlists configuration
  playlist_dirs:                # A list of directories to scan for .m3u playlists.
    - '/path/to/my/playlists'
    - '/another/path/for/playlists'
```

### Environment variables

Some features require API keys or secrets, which should be configured as environment variables.
You can place these in a `.env` file in the directory where you run the `beet` command.

- `BEETSTREAMNEXT_KEY`: Secret key used to encrypt legacy passwords at rest.
- `LASTFM_API_KEY`: (Optional) to enable biographies, similar artist discovery, etc.

## Tested clients

BeetstreamNext is tested and working with:

#### Android
- [Symfonium](https://symfonium.app/)
- [Tempo](https://github.com/CappielloAntonio/tempo)
- [Tempus](https://github.com/eddyizm/tempus)
- [SubTune](https://github.com/TaylorKunZhang/SubTune)
- [GoSonic](https://play.google.com/store/apps/details?id=com.readysteadygosoftware.gosonic)
- [K-19 Player](https://github.com/ulysg/k19-player)
- [Ultrasonic](https://gitlab.com/ultrasonic/ultrasonic)
- [Subtracks](https://github.com/austinried/subtracks)

#### iOS/iPadOS/macOS
- [Amperfy](https://github.com/BLeeEZ/amperfy)
- [Submariner](https://github.com/SubmarinerApp/Submariner)
- [Supersonic](https://github.com/dweymouth/supersonic)

#### Linux / Windows
- [Feishin](https://github.com/jeffvli/feishin)

## TODO
- [ ] Docker image
- [ ] User management (create/delete) via the API (instead of CLI only)
- [ ] Maybe provide a direct `smartplaylist` query support for virtual playlists
- [ ] Scrobbling to Last.fm and other similar services
- [ ] Move now_playing into the db as a volatile table


## Missing endpoints

See [here](missing-endpoints.md) a (non-exhaustive) list

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.