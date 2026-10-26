# Notifications

**BeetstreamNext** can send a notification when something happens (a [Beets import](./beets-import.md), a blocked login, a finished health scan, a new share, a missing music folder...), through [**Apprise**](https://github.com/caronc/apprise).

Apprise supports most chat, push and email services (Discord, Telegram, Slack, Matrix, ntfy, Pushover, Gotify, email, generic webhooks, and [many more](https://appriseit.com/services/)).

## Installation

Apprise is an optional extra:

```bash
pip install beetstreamnext[notifications]
```

It is included in `beetstreamnext[all]` and in the default Docker image. Without it, the admin **Notifications** tab only shows an install hint, and nothing is ever sent.

## Notifications

A _notification_ is a message with:

| Field       | Description                                                                                                     |
|-------------|-----------------------------------------------------------------------------------------------------------------|
| **Type**    | What triggers it (see [below](#types))                                                                          |
| **Title**   | The message title, can use [variables](#variables)                                                              |
| **Body**    | The message body, can use [variables](#variables)                                                               |
| **URLs**    | One or more Apprise URLs, e.g. `discord://webhook_id/token`. The notification is sent to every URL in the list. |

You can create as many notifications as you like, including several of the same type (for instance to word the message differently on different services).

### In the config file

Notifications can also be declared in the config file:

```yaml
notifications:
  - event: import_completed
    title: "Imported {albums_imported} album(s)"  # optional
    body: "{path} took {duration}"                # optional
    urls:                                         # or a single `url:`
      - discord://webhook_id/token
    options:
      skip_if_unchanged: false
    enabled: true                                 # optional, overridable from the UI
```

* `event` can be any type (see just below): `import_started`, `import_completed`, `import_failed`, `import_needs_input`, `import_queue_aborted`, `login_blocked`, `health_scan_completed`, `health_scan_failed`, `maintenance_completed`, `maintenance_failed`, `share_created`, `share_accessed`, `storage_problem`, `server_started`

These are shown in the admin panel as _read-only_.

### Types

| Type                                        | Sent when                                                                                                              |
|---------------------------------------------|------------------------------------------------------------------------------------------------------------------------|
| **Import started**                          | A `beet import` run starts (from the admin panel, a pinned scan, a Subsonic client's `startScan`, or a watched folder) |
| **Import completed**                        | An import finishes with exit code `0`                                                                                  |
| **Import failed**                           | An import finishes with a non-zero exit code                                                                           |
| **Import needs input**                      | An interactive import is waiting for an answer (once each time it starts waiting)                                      |
| **Pinned scan aborted**                     | A queued [pinned folder](./beets-import.md) scan couldn't be started, and the rest of the queue was dropped            |
| **Login blocked**                           | Too many failed logins locked out a user / IP                                                                          |
| **Health scan completed**                   | A library health scan finishes (can be limited to scans where songs are flagged)                                       |
| **Health scan failed**                      | A library health scan couldn't run, or crashed                                                                         |
| **Maintenance task completed**              | A maintenance action from the admin panel (clear caches, database cleanup, offline files cleanup) succeeded            |
| **Maintenance task failed**                 | A maintenance action from the admin panel failed                                                                       |
| **Share created**                           | A user creates a share (from a Subsonic client)                                                                        |
| **Share accessed**                          | A share's public page is opened                                                                                        |
| **Storage problem**                         | Library files can't be found on disk (checked on startup and after each health scan)                                   |
| **Server started**                          | **BeetstreamNext** finished starting up (also after a restart)                                                         |

> [!NOTE]
> A quiet watched-folder import exits with `0` even when Beets skipped albums, so _Import completed_ doesn't mean everything was imported.

### Variables

Notification titles and bodies can contain `{variables}`, replaced when the notification is sent:

| Variable            | Value                                                                              |
|---------------------|------------------------------------------------------------------------------------|
| `{path}`            | The folder being imported                                                          |
| `{status}`          | `started`, `completed`, `failed`, `needs input`, `aborted`, `blocked` or `running` |
| `{exit_code}`       | The exit code of the import (empty until it has finished)                          |
| `{duration}`        | How long the import ran for, e.g. `1m 23s` (empty until it has finished)           |
| `{albums_imported}` | How many albums were added to the library (`Import completed` / `Import failed`)   |
| `{albums_skipped}`  | How many albums were skipped: no match, duplicates, or skipped by you              |
| `{imported_list}`   | Names of the imported albums (`Artist - Album`), one per line (first 5 only)       |
| `{skipped_list}`    | Names of the skipped albums, one per line (first 5 only)                           |
| `{ip}`              | The IP the failed logins came from (`Login blocked`)                               |
| `{username}`        | The username that was tried, empty when the whole IP was blocked (`Login blocked`) |
| `{failures}`        | The number of failed attempts that triggered the block (`Login blocked`)           |
| `{songs_checked}`   | Songs checked by the scan (`Health scan completed`)                                |
| `{songs_flagged}`   | Songs currently flagged in the whole library (`Health scan completed`)             |
| `{songs_unchanged}` | Songs skipped because they hadn't changed since the last scan                      |
| `{time}`            | When the notification was sent                                                     |

### Album results

**BeetstreamNext** loads a small helper plugin (`beetstreamnext_events`) in the `beet import` process, which reports each album's outcome

- Only albums are counted, not singletons (`beet import -s`).

- This only covers imports run by **BeetstreamNext**, not the ones you run from your commandline or other tools.

- Skipped albums are listed with a reason: skipped by you, no match found, best match not confident enough, or already in the library.

- If you use my [`cataloghint`](https://github.com/FlorentLM/beets-cataloghint) plugin, its explanation is used when it is what made the match unconfident.

> [!NOTE]
> This companion plugin is automatically injected in the `beets import` process started by **BeetstreamNext**, no change are made to your beets config.

## Remarks

- URLs often contain tokens or passwords: they are stored **encrypted** in **BeetstreamNext**'s database, with the `BEETSTREAMNEXT_KEY` that protects other secrets (see [Installation](../installation.md)). They _are_ shown in the admin panel though (for now).
- Notifications are currently only managed through the admin panel, they can't be set via [configuration setting](../configuration.md) or YAML block (yet).
