#!/usr/bin/env python3
"""Regenerates docs/src/configuration.md from SETTINGS_SCHEMA.

    uv run python docs/regen_config_doc.py          # regenerate the file
    uv run python docs/regen_config_doc.py --check  # exit 1 if the file is out of date, don't write
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from beetsplug.beetstreamnext.schemas import SETTINGS_SCHEMA

OUT_PATH = Path(__file__).resolve().parent / 'src' / 'configuration.md'

# (Schema category key, Doc heading) in display order
CATEGORIES = [
    ('server', 'Server & network'),
    ('library', 'Library & metadata'),
    ('podcasts', 'Podcasts'),
    ('audio', 'Audio & jukebox'),
    ('security', 'Security'),
]

HEADER = """# Configuration reference

BeetstreamNext settings can be set from various ways:

1. Explicit sources: 
   - via CLI flag
   - via an environment variable
   - in **plugin mode**: via **Beets' `config.yaml`**, under a `beetstreamnext:` block (see [Installation](./installation.md#plugin-mode))
   - in **standalone mode**: via **BeetstreamNext's own config YAML (see [Installation](./installation.md#standalone-mode))
2. **The Admin WebUI**: Only for settings that aren't already pinned by one of the _explicit_ sources above. When a setting is pinned by any of those sources, its field in the Admin panel shows as locked/disabled.

Settings resolve order (first one set takes precedence):

- in **plugin mode**:

        CLI flag > environment variable > Beets' config's `beetstreamnext:` block > WebUI setting > Built-in default

- in **standalone mode**:

        CLI flag > environment variable > BeetstreamNext's YAML config file > Beets' config file (if you point it at one, and only for `library-db`/`music-root`) > WebUI setting > Built-in default

List-based settings (like for example `ip_whitelist`/`ip_blacklist`) are a bit more flexible: only the individual entries coming from an _explicit_ source are pinned, but you can still add/remove other entries on top of them from the Admin panel.

Settings marked <span style="color:#fab915">**requires restart**</span> only take effect after the server is restarted. Settings marked <span style="color:#6b5c58">**standalone only**</span> are not used when running as a Beets plugin."""

BADGES = {
    'standalone_only': ('#6b5c58', 'standalone only'),
    'sensitive': ('#6b5c58', 'sensitive'),
    'requires_restart': ('#fab915', 'requires restart'),
}


def badges_line(descriptor: dict) -> str | None:
    spans = [
        f'<span style="color:{color}">**{label}**</span>'
        for flag, (color, label) in BADGES.items()
        if descriptor.get(flag)
    ]
    return ', '.join(spans) if spans else None


def default_n_choices(descriptor: dict) -> str:
    default = descriptor.get('default')

    if default == '' or default == []:
        rendered = '*(empty)*'
    elif isinstance(default, bool) or isinstance(default, list):
        rendered = f'`{default!r}`'
    else:
        rendered = f'`{default}`'

    line = f'*default:* {rendered}'
    choices = descriptor.get('choices')
    if choices:
        line += ', *choices:* ' + ', '.join(f'`{c}`' for c in choices)
    return line


def render_setting(key: str, descriptor: dict) -> str:
    description = ' '.join(descriptor['description'].split())

    paragraphs = [f'### `{key}`', description]

    badges = badges_line(descriptor)
    if badges:
        paragraphs.append(badges)

    paragraphs.append(f"*type:* `{descriptor['type']}`")
    paragraphs.append(default_n_choices(descriptor))

    env_var = descriptor.get('env_var')
    if env_var:
        paragraphs.append(f'*env:* `{env_var}`')

    return '\n\n'.join(paragraphs)


def render_category(category: str, heading: str) -> str | None:
    entries = [
        render_setting(key, descriptor)
        for key, descriptor in SETTINGS_SCHEMA.items()
        if descriptor['category'] == category
    ]
    if not entries:
        return None
    return f'## {heading}\n\n' + '\n\n---\n\n'.join(entries)


def render() -> str:
    known_categories = {c for c, _ in CATEGORIES}
    unmapped = {d['category'] for d in SETTINGS_SCHEMA.values()} - known_categories
    if unmapped:
        raise ValueError(f'Setting(s) use categor(y/ies) not in CATEGORIES: {sorted(unmapped)}')

    sections = [HEADER]
    for category, heading in CATEGORIES:
        section = render_category(category, heading)
        if section:
            sections.append(section)

    return '\n\n'.join(sections) + '\n'


def main() -> int:
    content = render()

    if '--check' in sys.argv[1:]:
        current = OUT_PATH.read_text() if OUT_PATH.exists() else None
        if current != content:
            print(f'{OUT_PATH} is out of date, run: uv run python docs/regen_config_doc.py')
            return 1
        print(f'{OUT_PATH} is up to date.')
        return 0

    OUT_PATH.write_text(content)
    print(f'Wrote {OUT_PATH}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
