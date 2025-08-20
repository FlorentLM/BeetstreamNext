<br />

<div align="center">

  <a href="https://github.com/FlorentLM/pytinybvh">
    <img src="beetstreamnext.svg" alt="Logo" width="128" height="128">
  </a>

<h3 align="center">BeetstreamNext</h3>
  <p>
  BeetstreamNext exposes your Beets.io database with the OpenSubsonic API
  <br/>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)  

  </p>
</div>


BeetstreamNext is a fork of Beetstream, a [Beets.io](https://beets.io) plugin that exposes [OpenSubsonic API endpoints](https://opensubsonic.netlify.app/docs/opensubsonic-api/), allowing you to stream your Beets library.

## Motivation

I started implementing new features to Beetstream but ended up rewriting a significant part of it, so I figured it'd make more sense to keep it as a distinct project.
The goal is to cover all of the modern OpenSubsonic API, with some additions. I'm getting there :)

Personally, I use Beets to manage my music library but I don't like to write metadata to the files. So with this, I can have the best of both worlds.

## Install & Run

Requires Python 3.8 or newer.

1) First of all, you need to [install Beets](https://beets.readthedocs.io/en/stable/guides/main.html):
2) Install the dependancies with:

```
$ pip install beetstreamnext
```

3) Enable the plugin for Beets in your config file `~/.config/beets/config.yaml`:
```yaml
plugins: beetstreamnext
```

4) **Optional** You can change the host and port in your config file `~/.config/beets/config.yaml`.  
You can also chose to never re-encode files even if the clients asks for it with the option `never_transcode: True`. This can be useful if you have a weak CPU or a lot of clients.

Here are the default values:
```yaml
beetstreamnext:
  host: 0.0.0.0
  port: 8080
  never_transcode: False
```

5) Other configuration parameters:

If `fetch_artists_images` is enabled, BeetstreamNext will fetch the artists photos to display in your client player (if you enable this, it is recommended to also enable `save_artists_images`).

BeetstreamNext supports playlists from Beets' [playlist](https://beets.readthedocs.io/en/stable/plugins/playlist.html) and [smartplaylist](https://beets.readthedocs.io/en/stable/plugins/smartplaylist.html) plugins. You can also define a BeetstreamNext-specific playlist folder with the `playlist_dir` option:
```yaml
beetstreamnext:
  fetch_artists_images: False   # Whether BeetstreamNext should fetch artists photos when clients request them
  save_artists_images: False    # Save artists photos to their respective folders in your music library
  playlist_dir: './path/to/playlists'  # A directory with BeetstreamNext-specific playlists
```

6) Run with:
```
$ beet beetstreamnext
```

## Clients Configuration

### Authentication

There is currently no security. You can put whatever user and password you want in your favorite app.
But this is going to change soon.

### Server and Port

Currently runs on port `8080` (i.e.: `https://192.168.1.10:8080`)

## Supported Clients

All clients below have been tested and are working with this server. But in theory any Subsonic-compatible player should work.

### Android

- [Synfonium](https://symfonium.app/)
- [Tempo](https://github.com/CappielloAntonio/tempo)
- [SubTune](https://github.com/TaylorKunZhang/SubTune)
- [Subtracks](https://github.com/austinried/subtracks)
- [K-19 Player](https://github.com/ulysg/k19-player)
- [substreamer](https://substreamerapp.com/)
- [GoSONIC](https://play.google.com/store/apps/details?id=com.readysteadygosoftware.gosonic&hl=en_GB)
- [Ultrasonic](https://gitlab.com/ultrasonic/ultrasonic)

### Desktop

- [Supersonic](https://github.com/dweymouth/supersonic)

## Roadmap

- [ ] Finalise BeetstreamNext's database storage (for multiple users etc)
- [ ] Finalise authentication (needs database to be fully operational)
- [ ] Implement missing endpoints
- [ ] Create a Docker image
- [ ] Cleanup the README and update the installation instructions