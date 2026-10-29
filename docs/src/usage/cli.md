# CLI

**BeetstreamNext** can be started via two different entrypoints (via `beet beetstreamnext` in plugin mode, or via `beetstreamnext` in standalone mode, see [Installation](../installation.md)).

However, the command-line surface underneath is the same in both modes (user management, cache clearing, server flags).

## Plugin mode

In **plugin mode**, everything goes through Beets' `beet beetstreamnext` subcommand:

```bash
beet beetstreamnext [options]
```

| Flag                           | Effect                                                                                                     |
|--------------------------------|------------------------------------------------------------------------------------------------------------|
| *(none)*                       | Start the server                                                                                           |
| `--host HOST[,HOST...]`        | Override `host` for this run (comma-separated for multiple)                                                |
| `--port PORT`                  | Override `port` for this run                                                                               |
| `--threads N`                  | Override how many threads the Waitress worker uses for this run                                            |
| `--debug`                      | Run in Flask debug mode                                                                                    |
| `--force-trust-host`           | Force debug mode even when not bound to localhost (_NOT RECOMMENDED_)                                      |
| `-c`, `--create-user`          | Create a new user (interactive prompts)                                                                    |
| `--noinput`                    | With `--create-user`: non-interactive, see [Unattended first run](../installation.md#unattended-first-run) |
| `-u`, `--update-user USERNAME` | Update an existing user's roles (interactive prompts)                                                      |
| `-d`, `--delete-user USERNAME` | Delete a user (asks for confirmation)                                                                      |
| `-p`, `--password USERNAME`    | Change a user's password (interactive prompts)                                                             |
| `--list-users`                 | List all registered users and their roles                                                                  |
| `--clear-cache`                | Clear the thumbnail and HTTP caches                                                                        |
| `--backup FILE`                | Back up settings and data to `FILE` (`.tar.gz`), or into a directory.                                      |
| `--restore FILE` [`--yes`]     | Restore from a backup (the server must be stopped).                                                        |
| `--rekey` [`--yes`]            | Re-encrypt the database with a new `BEETSTREAMNEXT_KEY` (the server must be stopped).                      |

> [!NOTE]
> User-management or cache flags perform the action and exit immediately. They do _not_ start the server.

Settings not overridden by a flag come from Beets' `config.yaml` (under the `beetstreamnext:` key), see the [configuration reference](../configuration.md).

## Standalone mode

```bash
beetstreamnext [command] [username] [options]
```

| Command                  | Effect                                                                               |
|--------------------------|--------------------------------------------------------------------------------------|
| `run`                    | Start the server (default)                                                           |
| `create-user`            | Create a new user (interactive prompts)                                              |
| `update-user [username]` | Update an existing user's roles (interactive prompts)                                |
| `delete-user [username]` | Delete a user (asks for confirmation)                                                |
| `passwd [username]`      | Change a user's password (interactive prompts)                                       |
| `list-users`             | List all registered users and their key roles                                        |
| `clear-cache`            | Clear the thumbnail and HTTP caches                                                  |
| `backup FILE`            | Back up settings and data to `FILE` (`.tar.gz`), or into a directory                 |
| `restore FILE` [`--yes`] | Restore from a backup (the server must be stopped)                                   |
| `rekey` [`--yes`]        | Re-encrypt the database with a new `BEETSTREAMNEXT_KEY` (the server must be stopped) |

> [!NOTE]
> `command` defaults to `run` if omitted

<br>
<br>

| Flags                   | Env var            | Effect                                                                                                           |
|-------------------------|--------------------|------------------------------------------------------------------------------------------------------------------|
| `--config PATH`         | —                  | BeetstreamNext YAML config file (default location is OS-dependent, see [here](../installation.md))               |
| `--library-db PATH`     | `BEETS_LIBRARY_DB` | Path to the Beets `library.db`                                                                                   |
| `--music-root PATH`     | `MUSIC_ROOT`       | Music root directory (where song paths are relative to)                                                          |
| `--bsn-db PATH`         | `BSN_DB_PATH`      | Path to BeetstreamNext's database (default: alongside `library.db`, or `/config` in Docker)                      |
| `--beets-config PATH`   | `BSN_BEETS_CONFIG` | Optional Beets config file, for path formats/plugins only (default: `$BEETSDIR/config.yaml`, if set and present) |
| `--host HOST[,HOST...]` | `BSN_HOST`         | Host(s) to listen on                                                                                             |
| `--port PORT`           | `BSN_PORT`         | Port to listen on                                                                                                |
| `--threads N`           | `BSN_THREADS`      | Waitress worker threads                                                                                          |
| `--debug`               | —                  | Run in Flask debug mode                                                                                          |
| `--force-trust-host`    | —                  | Force debug mode even when not bound to localhost (_NOT RECOMMENDED_)                                            |
| `--as-password`         | —                  | With `create-app-password`: also usable as a password by legacy clients                                          |
| `--noinput`             | —                  | With `create-user`: non-interactive, see [Unattended first run](../installation.md#unattended-first-run)         |

See the [configuration reference](../configuration.md) for the full settings precedence rules, or [Installation](../installation.md#standalone-mode) for more on `library-db`/`music-root` specifically.

## Interactive prompts

`create-user`/`--create-user`, `update-user`/`--update-user`, `passwd`/`--password`, and `delete-user`/`--delete-user` all prompt interactively on the terminal:

- **Create**: Asks for a username (flags invalid characters and offers a sanitized alternative), a password, and asks you if the new user should be an admin or not.

- **Update**: Walks through every role one at a time (press Enter to leave a role unchanged, or `y`/`n` to enable/disable it).

- **Password change**: Asks for the new password.

- **Delete**: Asks for a confirmation before removing the account.

The interactivity is _deliberate_: these commands touch credentials, so user input is mandatory. Only exception to this is the [Unattended first run](../installation.md#unattended-first-run).

---
## Backup and restore

`backup` writes a snapshot of **BeetstreamNext**'s database (settings, users, app passwords, avatars, likes, ratings, bookmarks, playlists, shares, radio stations, podcasts, etc) to a `.tar.gz`.

```bash
beetstreamnext backup /backups/                      # -> /backups/beetstreamnext-backup-<timestamp>.tar.gz
beet beetstreamnext --backup /backups/bsn.tar.gz     # plugin mode
```

`restore` replaces the current database with the one in the archive. **The server must not be running.** The previous database is kept next to it as `beetstreamnext.db.pre-restore-<timestamp>`.

```bash
beetstreamnext restore /backups/bsn.tar.gz [--yes]
```

- The archive contains password hashes: keep it private (it is created `0600`).

- `BEETSTREAMNEXT_KEY` is **not** included. Store it separately.

- To restore a backup made with a different key, see [Changing the key](#changing-the-key).

- Your Beets `library.db` and music files are not part of the backup. Thumbnail/HTTP caches are not part of it either.

## Changing the key

`rekey` allows you to update the database's encryption key to a new one. **The server must not be running.**

A copy of the database from before the change is kept as `beetstreamnext.db.pre-rekey-<timestamp>`. That copy stays readable with the old key.

```bash
export BEETSTREAMNEXT_KEY=<new key>
export BEETSTREAMNEXT_OLD_KEY=<old key>     # or leave unset to be prompted
beetstreamnext rekey [--yes]
beet beetstreamnext --rekey [--yes]         # plugin mode
```

To restore a backup made with another key, set `BEETSTREAMNEXT_OLD_KEY` to the backup's key when running `restore`. The backup is re-encrypted with the current `BEETSTREAMNEXT_KEY` before it replaces the database:

```bash
export BEETSTREAMNEXT_KEY=<key to use from now on>
export BEETSTREAMNEXT_OLD_KEY=<key the backup was made with>
beetstreamnext restore /backups/bsn.tar.gz
```

If `BEETSTREAMNEXT_OLD_KEY` is not set and you run `restore` from a terminal, you are prompted for it.