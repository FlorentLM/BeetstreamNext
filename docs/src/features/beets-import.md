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

## Skipped folders and retries

Beets' `incremental` import remembers the folders it has seen (to avoid retrying them). By default, that includes folders that were _skipped_ (manually or automatically).

Beets' option `incremental_skip_later` turns that off, and makes every incremental run retry all the folders that were skipped.

**BeetstreamNext** does not depend on that option:

- **Watched folders, pinned scans and `startScan`** always remember the folders they skip, so an album that couldn't be matched is not retried over and over.

- **Manual imports and Reimports** (see below) always retry the folder they correspond to, even if it was imported or skipped before. Note that when a folder _contains_ several already-imported albums, Beets will ask about each of them (as it would do for any non-incremental import).

- **The import history** allows you to retry skipped albums (as an interactive import) so you can choose the match yourself.

> [!IMPORTANT]
> When Beets merges a disc into an album already in the library (`merge` duplicate action), it saves the result under a weird list of folders and files. Single disc folders are not remembered.
>
> The helper plugin records each folder separately so the watcher doesn't look at them again.


## Editing the Beets config

The Beets tab shows your Beets `config.yaml`.

Editing it from the Admin WebUI is restricted by default for security. You can allow this by enabling the [`allow_beets_config_edit`](../configuration.md#library--metadata) setting or by setting its corresponding environment variable.

> [!WARNING]
> A Beets config can load plugins from any path and run shell commands when an import runs. Whoever can edit it can therefore potentially run code on the server (as the user beets runs as).
>
> Only enable it if you trust every admin account _and the network_ they reach the panel from (i.e. via HTTPS, and ideally using the `admin_hostname` restriction).

### Blocked settings

Even with editing enabled, the config editor refuses to save any change that adds or modifies a setting that's able to run code or commands.

| Kind                   | Blocked                                                                                                                                                          |
|------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Plugins                | `inline` and `hook` (can't be added to `plugins`)                                                                                                                |
| Top-level keys         | `pluginpath`, `item_fields`, `album_fields`                                                                                                                      |
| Command/binary options | `hook.hooks`, `convert.command`, `convert.formats`, `play.command`, `play.raw`, `badfiles.commands`, `replaygain.command`, `keyfinder.bin`, `absubmit.extractor` |

- The YAML is parsed with a safe loader so Python-specific tags are never evaluated
- Entries that were **already in the file are trusted** as long as you leave them _unchanged_, so a config that legitimately uses `convert.command` can still be edited around it. Changing or adding one is refused, and the response lists the offending names

> [!IMPORTANT]
> This is not a sandbox: it can't stop someone with access to the file on disk, and it only knows about the options listed above. Third-party plugins that can execute commands are not covered.

## Disk writes

Imports that write tags, or that copy/move files, need [`allow_disk_writes`](../configuration.md#library--metadata) enabled. This isn't required if your Beets config has `write`, `copy` and `move` all disabled.

This also counts some plugins as disk-writing when they are loaded:

| Plugin     | Considered unsafe when      |
|------------|-----------------------------|
| `scrub`    | `scrub.auto` is on          |
| `embedart` | `embedart.auto` is on       |
| `fetchart` | `fetchart.auto` is on       |
| `hook`     | always                      |

If the config can't be resolved, the import is treated as unsafe and requires  [`allow_disk_writes`](../configuration.md#library--metadata).

## Notifications

**BeetstreamNext** can send an [Apprise notification](./notifications.md) when an import starts, finishes, fails or needs input.
