# Notifications

**BeetstreamNext** can send a notification when something happens during a [Beets import](./beets-import.md), through [**Apprise**](https://github.com/caronc/apprise).

Apprise supports most chat, push and email services (Discord, Telegram, Slack, Matrix, ntfy, Pushover, Gotify, email, generic webhooks, and [many more](https://appriseit.com/services/)).

## Installation

Apprise is an optional extra:

```bash
pip install beetstreamnext[notifications]
```

It is included in `beetstreamnext[all]` and in the default Docker image. Without it, the admin **Notifications** tab only shows an install hint, and nothing is ever sent.

## Notifications

A _notification_ is one saved message, with:

| Field       | Description                                                                                                     |
|-------------|-----------------------------------------------------------------------------------------------------------------|
| **Type**    | What triggers it (see [below](#types))                                                                          |
| **Title**   | The message title, can use [variables](#variables)                                                              |
| **Body**    | The message body, can use [variables](#variables)                                                               |
| **URLs**    | One or more Apprise URLs, e.g. `discord://webhook_id/token`. The notification is sent to every URL in the list. |

You can create as many notifications as you like, including several of the same type (for instance to word the message differently on different services), or a single one with several URLs if you just want the same message on several services.

### Types

| Type                      | Sent when                                                                                                               |
|---------------------------|-------------------------------------------------------------------------------------------------------------------------|
| **Import started**        | A `beet import` run starts (from the admin panel, a pinned scan, a Subsonic client's `startScan`, or a watched folder)  |
| **Import completed**      | An import finishes with exit code `0`                                                                                   |
| **Import failed**         | An import finishes with a non-zero exit code                                                                            |
| **Import needs input**    | An interactive import is waiting for an answer (once each time it starts waiting)                                       |
| **Pinned scan aborted**   | A queued [pinned folder](./beets-import.md) scan couldn't be started, and the rest of the queue was dropped             |

> [!NOTE]
> Notifications are currently about full runs, not albums: a quiet watched-folder import exits with `0` even when Beets skipped albums, and is reported as completed. Per-album results are planned.

### Variables

Titles and bodies can contain `{variables}`, replaced when the notification is sent:

| Variable      | Value                                                                    |
|---------------|--------------------------------------------------------------------------|
| `{path}`      | The folder being imported                                                |
| `{status}`    | `started`, `completed`, `failed`, `needs input` or `aborted`             |
| `{exit_code}` | The exit code of the import (empty until it has finished)                |
| `{duration}`  | How long the import ran for, e.g. `1m 23s` (empty until it has finished) |
| `{time}`      | When the notification was sent                                           |

## Remarks

- URLs often contain tokens or passwords: they are stored **encrypted** in **BeetstreamNext**'s database, with the `BEETSTREAMNEXT_KEY` that protects other secrets (see [Installation](../installation.md)). They _are_ shown in the admin panel though (for now).
- Notifications are currently only managed through the admin panel, they can't be set via [configuration setting](../configuration.md) or YAML block (yet).
