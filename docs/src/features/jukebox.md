# Jukebox mode

Subsonic's [Jukebox](https://opensubsonic.netlify.app/docs/endpoints/jukeboxcontrol/) mode lets playback happen directly _on the server_, with the Subsonic client app acting as the _remote control_ instead of streaming the audio itself.

**BeetstreamNext** exposes (in addition to the server's own hardware) two network backends: **Sonos** and **Chromecast**, allowing the audio to be played through a speaker on the server's local network.

## Backend choice

Set `jukebox_backend` to:

| Backend                       | Plays on                                                                                                          | Extra dependency                         |
|-------------------------------|-------------------------------------------------------------------------------------------------------------------|------------------------------------------|
| `server_hardware` _(default)_ | The server's own audio output (via [`mpv`](https://mpv.io/))                                                      | `mpv` binary on `PATH`                   |
| `sonos`                       | A Sonos speaker on the local network, via [**SoCo**](https://github.com/SoCo/SoCo)                                    | `pip install beetstreamnext[sonos]`      |
| `chromecast`                  | A Chromecast device on the local network, via [**pychromecast**](https://github.com/home-assistant-libs/pychromecast) | `pip install beetstreamnext[chromecast]` |

Jukebox mode must be enabled with the server-wide `jukebox_allowed` setting, and allowed per-user via that user's `jukeboxRole` (see [Accounts, permissions & chat](./accounts-and-permissions.md#roles)).

## Selecting a device

For any of the three backends, you can set _which_ device will play the audio with `jukebox_hardware_device`:

- for **`server_hardware`**: an mpv `--audio-device` value (e.g. `alsa/hw:0,0` on Linux, `coreaudio/BuiltInSpeakerDevice` on macOS, etc). Empty for the system default.
- for **`sonos`**: the Sonos speaker's IP address.
- for **`chromecast`**: the Chromecast device's UUID, or its IP address/hostname.

> **Note:** The admin panel also allows to scan for what devices are available on the current selected backend.

## Running in Docker

Both network device-discovery mechanisms (SSDP/UPnP for Sonos, mDNS/Zeroconf for Chromecast) rely on LAN multicast, which does not cross Docker's default `bridge` network.

If you're containerizing **BeetstreamNext**, you have a few options:

- **`network_mode: host`**: the container shares the host's network namespace, so multicast just works and "Discover devices" behaves like it would bare-metal.
  - Simplest option, but it's Linux-only in practice (Docker Desktop on macOS/Windows runs containers inside a VM, so host networking doesn't give the container real access to your LAN's multicast traffic).
- **A `macvlan`/`ipvlan` network**: the container has its own IP directly on the LAN, so it sits on the same layer as your Sonos/Chromecast devices and multicast works.
  - This needs a physical (usually wired) interface, as most Wi-Fi drivers/APs won't allow the extra MAC addresses macvlan relies on. Also you'd need an extra macvlan shim interface on the host (to still reach **BeetstreamNext**'s web UI locally). Bit of a hassle, but should be possible.
- **Don't use discovery**: set `jukebox_hardware_device` directly in the Web UI or in `config.yaml` (the speaker's IP for Sonos, the Chromecast's UUID/IP/hostname).

The `server_hardware` backend needs a real audio output device passed into the container:

- **ALSA** (`jukebox_hardware_device` like `alsa/hw:0,0`): pass the host's sound card in, and give the container access to it:

  ```yaml
  # ... in your docker-compose.yaml
      devices:
        - /dev/snd:/dev/snd
      group_add:
        - "29"   # The host's 'audio' group gid, you can double-check yours with `getent group audio`
  # ...
  ```

- **PulseAudio/PipeWire** (`jukebox_hardware_device` like `pulse`): bind-mount the host user's runtime socket, and point mpv at it:

  ```yaml
  # ... in your docker-compose.yaml
      volumes:
        - /run/user/1000/pulse:/run/pulse:ro   # Replace 1000 by the uid of the user who owns that socket on the host
      environment:
        PULSE_SERVER: unix:/run/pulse/native
  # ...
  ```