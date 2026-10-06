# Beets import

**BeetstreamNext** can run `beet import` for you from the [Web UI](../usage/webui.md#beets).

| Source                    | Interactive | Started from               | Beets flags                                      |
|---------------------------|-------------|----------------------------|--------------------------------------------------|
| Manually selected folders | Yes         | Web UI                     | none (beets asks you what to do)                 |
| Pinned folders            | No          | Web UI, Subsonic client    | `-q`, and `--incremental` _or_ `--noincremental` |
| Watched folders           | No          | Automatically on new files | `-q --incremental`                               |

Imports use your Beets config and library database (i.e. the same one that **BeetstreamNext** serves), so it includes all your preferred import settings, other plugins, etc.

> [!TIP]
> I recommend using my [**beets-cataloghint**](https://pypi.org/project/beets-cataloghint/) plugin for better MusicBrainz matches :)

## Manually selected folders

Enter a directory in the **Beets** tab and start the import. You get a terminal-like view of the session, and when Beets asks a question (apply, skip, pick another candidate, etc) you can type your answer, exactly like on the command line.

This is, to some extent, similar to what [**Betanin**](https://github.com/sentriz/betanin) does.

## Pinned folders

You can pin folders you regularly import from (a _downloads_ directory for instance) so they can be imported in one click.

More importantly, importing from your pinned folders can be triggered from a Subsonic client using the [`startScan`](../api-coverage.md) endpoint.

This is _non-interactive_: it runs in [quiet](https://beets.readthedocs.io/en/stable/reference/config.html#quiet) mode ([`-q`](https://beets.readthedocs.io/en/stable/reference/config.html#quiet)) and Beets never waits for input. It follows your config for [`quiet_fallback`](https://beets.readthedocs.io/en/stable/reference/config.html#quiet-fallback), [`timid`](https://beets.readthedocs.io/en/stable/reference/config.html#timid), and so on.

Pinned folders also have an **incremental** toggle that controls Beets' [`--incremental`](https://beets.readthedocs.io/en/stable/reference/config.html#incremental) / [`--noincremental`](https://beets.readthedocs.io/en/stable/reference/config.html#incremental) modes.

> [!NOTE]
> Beets imports recursively, so a pinned folder must not overlap your output music library directory (it can't be inside it or contain it).

## Watched folders (auto-import)

A pinned folder marked as **watched** is monitored ,and whenever new audio files are added to it, they are automatically imported.

- This needs the [`import_watch_enabled`](../configuration.md#library--metadata) setting to be on

- Folders are polled every 30 seconds

- A folder is only imported once its contents have stopped changing for [`import_watch_settle`](../configuration.md#library--metadata) seconds (60 by default) (this is to avoid importing half-copied or half-downloaded albums)

- If another import is running, the folder is queued

- Watched imports are always [quiet](https://beets.readthedocs.io/en/stable/reference/config.html#quiet) and [incremental](https://beets.readthedocs.io/en/stable/reference/config.html#incremental)

## Disk writes

Imports that write tags, or that copy/move files, need [`allow_disk_writes`](../configuration.md#library--metadata) enabled. This isn't required if your Beets config has `write`, `copy` and `move` all disabled.

## Notifications

**BeetstreamNext** can send an [Apprise notification](./notifications.md) when an import starts, finishes, fails or needs input.
