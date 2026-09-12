# Installation

BeetstreamNext can run in two modes:

- **Plugin mode**: Attaches to an existing [Beets](https://beets.io) install and reuses its `config.yaml`. This is the original, most-tested way to run it.
- **Standalone mode**: BeetstreamNext runs in its own process, pointed directly at a `library.db` (still `beets`-managed, just not invoked _as_ a Beets plugin). Useful when Beets itself lives in a different container or host than BeetstreamNext, e.g. a shared library managed by [Betanin](https://github.com/sentriz/betanin).

Both existing modes share the same [feature set](./features/index.md) and [settings](./configuration.md), only how you configure and launch the server differ slightly.

## Requirements

- Python 3.13+
- **Plugin mode** needs a working Beets install with a library you already import music into. **Standalone mode** needs `beets` available as a Python package (for its query engine and file-path handling).
- Optionally (but like, _highly_ recommended), [`ffmpeg`](https://ffmpeg.org/) installed and available on `PATH`.
- Optionally, [`mpv`](https://mpv.io/) installed and available on `PATH` for jukebox mode using the server's own audio hardware (not needed for the `sonos`/`chromecast` jukebox backends, or if you don't use jukebox mode. See [Jukebox](./features/jukebox.md) info).

- Both binaries can instead be pointed to explicitly via the `ffmpeg_path`/`mpv_path` settings if they aren't on your `PATH`.

## 1. Clone and install

```bash
git clone https://github.com/FlorentLM/BeetstreamNext.git
cd BeetstreamNext
pip install .
```

Optional extras pull in Python dependencies for specific features:

```bash
pip install .[wiki]              # Wikipedia artist-biographies (using wikipedia-api)
pip install .[podcasts]          # Podcast support (using feedparser for the RSS feeds)
pip install .[podcast-discovery] # Podcast channel discovery (using the Podcast Index API)
pip install .[radio-discovery]   # Internet radio station discovery (using the Radio Browser API)
pip install .[sonos]             # Sonos speaker jukebox backend (using SoCo)
pip install .[chromecast]        # Chromecast jukebox backend (using pychromecast)
pip install .[all]               # Installs all optional dependencies
```

(With `uv`, use for instance `uv sync --extra podcasts`, or `uv sync --extra all`)

This installs a `beetstreamnext` console command (used by **standalone mode**) alongside the `beetstreamnext` Beets plugin (used by **plugin mode**). These are two separate entrypoints sharing the same codebase.

## 2. Configure and run

### Plugin mode

Add `beetstreamnext` to the `plugins` line in Beets' `config.yaml`, and put any BeetstreamNext settings under a `beetstreamnext:` block in that file. See the [configuration reference](./configuration.md) for the full list of available settings:

```yaml
plugins: beetstreamnext

beetstreamnext:
  port: 8080
```

Then just run it through `beet`'s own subcommand:

```bash
beet beetstreamnext
```

Other plugin-mode flags: `--create-user`, `--update-user USERNAME`, `--delete-user USERNAME`, `--password USERNAME`, `--list-users`, `--clear-cache`, plus `--host`/`--port`/`--threads`/`--debug` to override those settings for a single run. See [CLI usage](./usage/cli.md#plugin-mode) for the full command reference.

### Standalone mode

Standalone mode resolves its Beets library path (`library-db`) and music root (`music-root`) following a specific order:

- `library_db`:

      `--library-db` CLI flag > BEETS_LIBRARY_DB env var > `library_db` in YAML > beets.config['library'] (only if `beets_config_path` was resolved) > Error

- `music_root`:

      `--music-root` CLI flag > MUSIC_ROOT env var > `music_root` in YAML > beets.config['directory'] (only if `beets_config_path` was resolved) > WebUI setting > Error

Every other setting follows the (roughly similar) order defined in [Configuration](./configuration.md) and can also be set from the Admin panel (except `library_path`, see **Note** below).

> **Note:** Since `--library-db`/`BEETS_LIBRARY_DB`/`library_db` (in the YAML) is required on every run just to locate BeetstreamNext's own database, there's no scenario where it isn't explicitly set, so it is currently never editable in the WebUI (I might revise this).

At minimum, point it at your `library.db` and music root:

```bash
beetstreamnext run --library-db /path/to/library.db --music-root /path/to/music
```

Or via a YAML config file. Pass `--config`, or drop it at the default location for your platform:

- Docker: `/config/beetstreamnext.yaml`
- Linux/macOS: `$XDG_CONFIG_HOME/beetstreamnext/beetstreamnext.yaml` (usually `~/.config/beetstreamnext/beetstreamnext.yaml`)
- Windows: `%APPDATA%\beetstreamnext\beetstreamnext.yaml`

```yaml
# beetstreamnext.yaml
library_db: /path/to/library.db
music_root: /path/to/music
port: 8080
```

```bash
beetstreamnext run --config /path/to/beetstreamnext.yaml
```

Or via environment variables (handy for containers):

```bash
export BEETS_LIBRARY_DB=/path/to/library.db
export MUSIC_ROOT=/path/to/music
beetstreamnext run
```

Other standalone subcommands: `create-user`, `update-user USERNAME`, `delete-user USERNAME`, `passwd USERNAME`, `list-users`, `clear-cache`. See [CLI usage](./usage/cli.md#standalone-mode) for the full flag list (`--bsn-db`, `--beets-config`, `--host`, `--port`, `--threads`, `--debug`, `--force-trust-host`).

> **Note:** If a Beets config file passed via `--beets-config` contains its own `beetstreamnext:` section, it is _ignored_ in standalone mode. Put those settings in a dedicated BeetstreamNext `--config` YAML file, or `BSN_*` environment variables, or set them via the Admin WebUI instead.

### Docker

There is a `Dockerfile` available at the repository root. There's no published image yet, so you need build it yourself:

```bash
docker build -t beetstreamnext .
```

Build-time options (`--build-arg`):

- `EXTRAS` (default `all`): which optional feature sets to install, comma-separated. Same list as [above](#1-clone-and-install): `wiki`, `podcasts`, `podcast-discovery`, `radio-discovery`, `sonos`, `chromecast`, or `all`.
- `BEETS_VERSION`: install this exact version of `beets` instead of whatever `pyproject.toml` would otherwise pick.
- `WITH_MPV` (default `false`): also install `mpv`. Only needed for the `server_hardware` jukebox backend, which isn't fully set up for Docker yet (see below), so leave this off unless you're experimenting.
- `WITH_DEBUG_TOOLS` (default `false`): also install `curl`, `wget`, `ping`, `dig`/`nslookup`, `nc`, and `ip`/`ss`, for poking at networking issues from inside the container (e.g. `docker exec -it beetstreamnext curl ...`). Off by default to keep the image lean; rebuild with `--build-arg WITH_DEBUG_TOOLS=true` when you actually need them.
- `PYTHON_VERSION` (default `3.13`): Python version to build against.

Run it like this:

```bash
docker run -d --name beetstreamnext \
  --restart unless-stopped \
  -p 8080:8080 \
  -e PUID=1000 -e PGID=1000 \
  -v /path/to/config:/config \
  -v /path/to/library.db:/data/library.db:ro \
  -v /path/to/music:/music:ro \
  -e BEETS_LIBRARY_DB=/data/library.db \
  -e MUSIC_ROOT=/music \
  beetstreamnext
```

- `/config` is where `beetstreamnext.yaml`, the `.env` file (holding `BEETSTREAMNEXT_KEY`, see [Encryption key](#3-encryption-key)), and BeetstreamNext's own database (`beetstreamnext.db`) all get created.
- `/cache` is scratch space. It's not necessary to mount, but you can (if you want the cache to survive restarts).
- `PUID`/`PGID` (default `1000`/`1000`) should match the user that owns your library/music files on the host.

> **Note:** BeetstreamNext never runs as root. The root user is only used at container start, to `chown` `/config` and `/cache` to that `PUID`/`PGID` before dropping to it for the rest of the process's life.

> **Note:** Don't mount your library/music paths at `/config` or `/cache`, or the startup `chown` will recursively re-own them. If you want to control the folders' ownership yourself, you can run the container as a specific user directly (use `docker run --user UID:GID` (in which case also make sure `/config` and `/cache` are already owned by that user), and the entrypoint will notice and won't try to switch users itself.

Other standalone subcommands work by overriding the container's command. For example, an [unattended first run](#unattended-first-run):

```bash
docker run --rm \
  -e BSN_ADMIN_USER=admin -e BSN_ADMIN_PASSWORD=hunter2 \
  -e BEETS_LIBRARY_DB=/data/library.db \
  -v /path/to/config:/config \
  -v /path/to/library.db:/data/library.db:ro \
  beetstreamnext create-user --noinput
```

> **Note:** `-v /path/to/config:/config` must point at the _same host path_ as the main `run` container above (the encryption key and the user this creates both get stored under `/config`, so the two runs need to share it to see the same user/key).

#### Who runs `beet`?

BeetstreamNext only reads the library. Something still has to run `beet import` or any other thing you want to do with Beets. Two ways to do that:

- **Use this container:** It already has `beets` installed, so you can use that directly. The image sets `BEETSDIR=/config/beets`, so `beet` (ran inside the container) reads/writes `/config/beets/config.yaml`.
  - BeetstreamNext itself picks up that same file automatically too (as a fallback `--beets-config`/`BSN_BEETS_CONFIG`), so as long as your beets `config.yaml` lives there, both tools agree on the library/music paths with no extra flags. Just create/edit `config.yaml` at `/path/to/config/beets/config.yaml` on the host (same volume as the one mounted at `/config` above).
  - I recommend adding `alias beet="docker exec -it --user beetstream beetstreamnext beet"` to your host's `.bashrc` / `.zshrc` for convenience. The `--user beetstream` is important here, since `docker exec` runs as root by default and you don't want `beet` writing root-owned files into your library/music paths.
  - If you'd rather keep an existing beets config file _elsewhere_ instead, you can point `--beets-config`/`BSN_BEETS_CONFIG` (and, if you also invoke `beet` in the container, `docker exec`'s `BEETSDIR` or `beet --config`) at it explicitly, which overrides the `/config/beets` default.

- **Use a separate beets install:** Point BeetstreamNext at a `library.db` managed elsewhere (your own machine, or in another container like [Betanin](https://github.com/sentriz/betanin)) by mounting the same files into both.

## 3. Encryption key

User passwords aren't hashed, they're stored _reversibly encrypted_, because Subsonic's legacy MD5-token auth requires the server to recompute `md5(password + salt)` on every login, which needs the plaintext password to be recoverable... The server key `BEETSTREAMNEXT_KEY` is that database encryption key.

This is meant to protect against the database file leaking on its own: a backup that includes `library.db`/`beetstreamnext.db` but not the dotfiles, a misconfigured endpoint serving the db file, a db copied to a new host without also copying its key, ...this sort of thing. It does **not** protect against a fully compromised filesystem: anyone who can read both the database *and* wherever the key lives can decrypt everything... but in a situation like this, your BeetstreamNext data is probably going to be the least of your worries :D

Given that, how the server key is provisioned matters:

- **You have real secrets management** (Docker secrets, systemd `LoadCredential`, Vault, a k8s Secret, etc): set `BEETSTREAMNEXT_KEY` as an environment variable yourself, however your setup injects secrets, *before* the first run. BeetstreamNext detects it and uses it: it's never written to disk.
- **You don't**: on first run, BeetstreamNext generates one, prints it _once_, and saves it to a `.env` file next to your database. Keep that file safe: set restrictive permissions if your platform doesn't already (it's created with `0600`), and make sure it's excluded from anywhere the database itself isn't equally protected (e.g. don't back up one without the other, etc.).

Either way: if the key is lost, stored passwords become unrecoverable and you'll need to delete the database and set up again.

> **Note:** You also need this server key during the first-run admin account creation if using the Web UI (see below).

## 4. First run

The first time the server starts with no users in the database, it walks you through creating the initial admin account:

- **Running interactively in a terminal** (the common case for both `beet beetstreamnext` and `beetstreamnext run`): you're prompted right there for a username and password, and the account is created automatically as an admin.
- **Running non-interactively** (a service manager, a container, anything without a TTY attached): Accessing the Web UI directs you to a setup wizard. That page asks for an admin username and password, plus the `BEETSTREAMNEXT_KEY` from the step above (to avoid letting anyone set up the account before you do).
- **Fully unattended** (for instance a Docker container with no TTY and nobody to click through a setup page): *before* starting the server, run `create-user --noinput` (with `BSN_ADMIN_USER`/`BSN_ADMIN_PASSWORD` set) as a separate, one-time step — see below.

Either way, the admin user account's API key is shown once, on creation. **Save it**. It's what you'll enter into a Subsonic client instead of a password when using API-key authentication.

Once at least one user exists, use `--create-user`/`create-user` (or the admin panel's Users tab) to create other user accounts (admins or not).

### Unattended first run

You can setup the first admin account in a completely unattended way.

Use `--create-user --noinput` (for **plugin mode**) or `create-user --noinput` (for **standalone mode**)

This reads the `BSN_ADMIN_USER`/`BSN_ADMIN_PASSWORD` env vars, creates that one admin account, prints its API-key, and exits (it doesn't start the server).

```bash
BSN_ADMIN_USER=admin BSN_ADMIN_PASSWORD=hunter2 beetstreamnext create-user --noinput --library-db /path/to/library.db
```

```bash
BSN_ADMIN_USER=admin BSN_ADMIN_PASSWORD=hunter2 beet beetstreamnext --create-user --noinput
```

(obviously replace `admin` and `hunter2` by your chosen admin username and password)

> **Note:** This is a _one-time_ step. It will refuse to run if any user account already exists.

## 5. Normal startup

By default the server listens on `0.0.0.0:8080`. Open `http://<host>:8080` to see the public homepage, where you can log into the admin dashboard (see [Web UI usage](usage/webui.md)).

Point any Subsonic/OpenSubsonic/Navidrome client (see [tested clients](./clients.md)) at that same address. You can use the admin account directly, or a non-admin user account you create afterwards if you wish.

---

See the [configuration reference](../configuration.md) for any of the settings mentioned here.