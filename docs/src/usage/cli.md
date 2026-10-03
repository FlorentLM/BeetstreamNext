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

> **Note:** User-management or cache flags do not start the server.

Settings not overridden by a flag come from Beets' `config.yaml` (under the `beetstreamnext:` key), see the [configuration reference](../configuration.md).

## Standalone mode

```bash
beetstreamnext [command] [username] [options]
```

| Command                  | Effect                                                |
|--------------------------|-------------------------------------------------------|
| `run`                    | Start the server (default)                            |
| `create-user`            | Create a new user (interactive prompts)               |
| `update-user [username]` | Update an existing user's roles (interactive prompts) |
| `delete-user [username]` | Delete a user (asks for confirmation)                 |
| `passwd [username]`      | Change a user's password (interactive prompts)        |
| `list-users`             | List all registered users and their key roles         |
| `clear-cache`            | Clear the thumbnail and HTTP caches                   |

> **Note:** `command` defaults to `run` if omitted

<br>
<br>

| Flags                   | Env var            | Effect                                                                                                           |
|-------------------------|--------------------|------------------------------------------------------------------------------------------------------------------|
| `--config PATH`         | —                  | BeetstreamNext YAML config file (default location is OS-dependent, see [here](../installation.md))               |
| `--library-db PATH`     | `BEETS_LIBRARY_DB` | Path to the Beets `library.db`                                                                                   |
| `--music-root PATH`     | `MUSIC_ROOT`       | Music root directory (where song paths are relative to)                                                          |
| `--bsn-db PATH`         | `BSN_DB_PATH`      | Path to BeetstreamNext's own database (default: alongside `library.db`, or `/config` in Docker)                  |
| `--beets-config PATH`   | `BSN_BEETS_CONFIG` | Optional Beets config file, for path formats/plugins only (default: `$BEETSDIR/config.yaml`, if set and present) |
| `--host HOST[,HOST...]` | `BSN_HOST`         | Host(s) to listen on                                                                                             |
| `--port PORT`           | `BSN_PORT`         | Port to listen on                                                                                                |
| `--threads N`           | `BSN_THREADS`      | Waitress worker threads                                                                                          |
| `--debug`               | —                  | Run in Flask debug mode                                                                                          |
| `--force-trust-host`    | —                  | Force debug mode even when not bound to localhost (_NOT RECOMMENDED_)                                            |
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