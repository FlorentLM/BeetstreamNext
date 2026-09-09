# Jukebox mode

Subsonic's [Jukebox](https://opensubsonic.netlify.app/docs/endpoints/jukeboxcontrol/) mode lets playback happen directly **on the server**, with the Subsonic client app acting as a **remote control** instead of streaming audio itself.

**BeetstreamNext** exposes (in addition to the server's own hardware), two network backends, **Sonos** and **Chromecast**, allowing the audio to be played through a speaker on the server's local network.

## Backend choice

Set `jukebox_backend` to one of:

| Backend                       | Plays on                                                                                                          | Extra dependency                         |
|-------------------------------|-------------------------------------------------------------------------------------------------------------------|------------------------------------------|
| `server_hardware` _(default)_ | The server's own audio output, via [`mpv`](https://mpv.io/)                                                       | `mpv` binary on `PATH`                   |
| `sonos`                       | A Sonos speaker on the local network, via [SoCo](https://github.com/SoCo/SoCo)                                    | `pip install beetstreamnext[sonos]`      |
| `chromecast`                  | A Chromecast device on the local network, via [pychromecast](https://github.com/home-assistant-libs/pychromecast) | `pip install beetstreamnext[chromecast]` |

Jukebox mode must be explicitly allowed with the server-wide `jukebox_allowed` setting, and per-user via that user's `jukeboxRole` (see [Accounts, permissions & chat](./accounts-and-permissions.md#roles)).

## Selecting a device

You can set which device will play the audio with `jukebox_hardware_device`, which can be:

- for **`server_hardware`**: an mpv `--audio-device` value (e.g. `alsa/hw:0,0` on Linux, `coreaudio/BuiltInSpeakerDevice` on macOS, etc), or empty for the system default
- for **`sonos`**: the speaker's IP address
- for **`chromecast`**: the device's UUID

The admin panel also allows to scan for what devices are available and lets you pick one.

## Running in Docker

Both network device-discovery mechanisms (SSDP/UPnP for Sonos, mDNS/Zeroconf for Chromecast) rely on LAN multicast, which does not cross Docker's default `bridge` network. If you're containerizing BeetstreamNext:

- Use `network_mode: host` if you want the "Discover devices" scan to work.
- Otherwise, set `jukebox_hardware_device` directly in the Web UI or in `config.yaml`.

The `server_hardware` backend additionally needs a real audio output device passed into the container (e.g. `/dev/snd` + ALSA, or a bind-mounted PulseAudio/PipeWire socket).

> **Note:** Jukebox mode has been tested on bare-metal installs for all three backends, but Docker networking/audio passthrough is still being worked out.

## Other planned backends

Snapcast, DLNA/UPnP AV, and AirPlay support are planned but not yet implemented.
