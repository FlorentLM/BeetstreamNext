# BeetstreamNext

**BeetstreamNext** exposes your **[Beets](https://beets.io)** music library over the **[OpenSubsonic API](https://opensubsonic.netlify.app/)**, letting you stream your music to any Subsonic/OpenSubsonic/Navidrome-compatible client.

Essentially all of the Subsonic/OpenSubsonic API specification is supported, including its extensions. Only the video-streaming functionalities are excluded (see [API coverage](./api-coverage.md)).

**BeetstreamNext** also reaches beyond your **Beets** library to provide metadata augmentation (artist biographies and pictures, Discogs ratings, etc...) and additional reliability/security tweaks.

See [Features](./features/index.md) for the full detailed view.

## Docs overview

- **[Features](./features/index.md)**: full API coverage (including internet radio, podcasts, playlists, public shares, multi-user accounts & permissions, jukebox mode...), along with BeetstreamNexts's own additional features.
- **[Installation](./installation.md)**: Install as a Beets plugin or as a standalone server.
- **[Usage](./usage/cli.md)**: The [CLI](./usage/cli.md) commands (plugin mode and standalone mode) and a quick tour of the [Web UI](usage/webui.md).
- **[Configuration reference](./configuration.md)**: Every setting by category, plus instructions on [reverse proxy & CORS](./reverse-proxy.md) setup.
- **[Reference](./api-coverage.md)**: Details of [API coverage](./api-coverage.md) and [tested clients](./clients.md).

## Project history

BeetstreamNext originated as a fork of [Beetstream](https://github.com/BinaryBrain/Beetstream). It has since fully separated and shares almost no code with the original, but it's still worth crediting :)

## License

BeetstreamNext is MIT-licensed. See the [LICENSE](https://github.com/FlorentLM/BeetstreamNext/blob/main/LICENSE) file for details.
