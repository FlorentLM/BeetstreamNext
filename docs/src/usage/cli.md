# CLI

BeetstreamNext can be started via two different entrypoints (via `beet beetstreamnext` in **plugin mode**, or via `beetstreamnext` in **standalone mode**, see [Installation](../installation.md)), but the command-line surface underneath is the same in both modes (user management, cache clearing, server flags).

## Plugin mode

In **plugin mode**, everything goes through Beets' own `beet beetstreamnext` subcommand:

```bash
beet beetstreamnext [options]
```

| Flag                           | Effect                                                                                         |
|--------------------------------|------------------------------------------------------------------------------------------------|
| *(none)*                       | Start the server                                                                               |
| `--host HOST[,HOST...]`        | Override `host` for this run (comma-separated for multiple)                                    |
| `--port PORT`                  | Override `port` for this run                                                                   |
| `--threads N`                  | Override how many threads the Waitress worker uses for this run                                |
| `--debug`                      | Run in Flask debug mode                                                                        |
| `--force-trust-host`           | Force debug mode even when not bound to localhost (_NOT_ recommended)                          |
| `-c`, `--create-user`          | Create a new user (interactive prompts)                                                        |
| `--noinput`                    | With `--create-user`: non-interactive, see [Unattended bootstrap](#unattended-bootstrap) below |
| `-u`, `--update-user USERNAME` | Update an existing user's roles (interactive prompts)                                          |
| `-d`, `--delete-user USERNAME` | Delete a user (asks for confirmation)                                                          |
| `-p`, `--password USERNAME`    | Change a user's password (interactive prompts)                                                 |
| `--list-users`                 | List all registered users and their roles                                                      |
| `--clear-cache`                | Clear the thumbnail and HTTP caches                                                            |

A user-management or cache flag runs that action and exits (it doesn't start the server).

Settings not overridden by a flag come from Beets' `config.yaml` (under the `beetstreamnext:` key) as usual, see the [configuration reference](../configuration.md).

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

| Flags                   | Env var            | Effect                                                                                       |
|-------------------------|--------------------|----------------------------------------------------------------------------------------------|
| `--config PATH`         | —                  | YAML config file (default location is OS-dependent, see [here](../installation.md))          |
| `--library-db PATH`     | `BEETS_LIBRARY_DB` | Path to the Beets `library.db`                                                               |
| `--music-root PATH`     | `MUSIC_ROOT`       | Music root directory (where song paths are relative to)                                      |
| `--bsn-db PATH`         | `BSN_DB_PATH`      | Path to BeetstreamNext's own database (default: alongside `library.db`)                      |
| `--beets-config PATH`   | `BSN_BEETS_CONFIG` | Optional Beets config file, for path formats/plugins only                                    |
| `--host HOST[,HOST...]` | `BSN_HOST`         | Host(s) to listen on                                                                         |
| `--port PORT`           | `BSN_PORT`         | Port to listen on                                                                            |
| `--threads N`           | `BSN_THREADS`      | Waitress worker threads                                                                      |
| `--debug`               | —                  | Run in Flask debug mode                                                                      |
| `--force-trust-host`    | —                  | Force debug mode even when not bound to localhost (_NOT_ recommended)                        |
| `--noinput`             | —                  | With `create-user`: non-interactive, see [Unattended bootstrap](#unattended-bootstrap) below |

See the [configuration reference](../configuration.md) for the full settings precedence rules, or [Installation](../installation.md#standalone-mode) for more on `library-db`/`music-root` specifically.

## Interactive prompts

`create-user`/`--create-user`, `update-user`/`--update-user`, `passwd`/`--password`, and `delete-user`/`--delete-user` all prompt on the terminal rather than taking everything as flags:

- **Create**: asks for a username (flags invalid characters and offers a sanitized alternative), a password (enforcing a minimum length), and asks you if the new user should be an admin or not.
- **Update**: walks through every role one at a time, showing its current state — press Enter to leave a role unchanged, or `y`/`n` to enable/disable it.
- **Password change**: asks for the new password.
- **Delete**: asks for a confirmation before removing the account.

This is _deliberate_: these commands touch credentials, and user input is mandatory. There is _one_ exception to this, the [unattended first run](../installation.md#unattended-first-run).

---