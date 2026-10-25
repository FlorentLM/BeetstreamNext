"""
Regenerates docs/src/configuration.md from SETTINGS_SCHEMA

    uv run python tools/regen_config_doc.py          # regenerate the file
    uv run python tools/regen_config_doc.py --check  # fail if the file is out of date, don't write
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from beetsplug.beetstreamnext.core.config.settings_schema import SETTINGS_SCHEMA

##

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / 'docs' / 'src' / 'configuration.md'

##

# (Schema category key, Doc heading) in display order
CATEGORIES = [
    ('connections', 'External services'),
    ('server', 'Server & network'),
    ('library', 'Library & metadata'),
    ('podcasts', 'Podcasts'),
    ('audio', 'Audio & jukebox'),
    ('security', 'Security'),
]

header = """# Configuration reference

BeetstreamNext settings can be set from various ways:

1. Explicit sources: 
   - via CLI flag
   - via an environment variable
   - in plugin mode: via **Beets**' `config.yaml`, under a `beetstreamnext:` block (see [Installation](./installation.md#plugin-mode))
   - in standalone mode: via **BeetstreamNext**'s config YAML (see [Installation](./installation.md#standalone-mode))

2. **The Admin WebUI**: Only for settings that aren't already pinned by one of the _explicit_ sources above. When a setting is pinned by any of those sources, its field in the Admin panel is locked/disabled.

Settings resolve order (first one set takes precedence):

- in **plugin mode**:

```mermaid
flowchart LR
    A("CLI flag") --> B("environment variable") --> C("Beets' config's <code>beetstreamnext:</code> block") --> D("WebUI setting") --> E("Built-in default")

style A fill:#ffffff12,stroke:#808080
style B fill:#ffffff12,stroke:#808080
style C fill:#ffffff12,stroke:#808080
style D fill:#ffffff12,stroke:#808080
style E fill:#ffffff12,stroke:#808080
```

- in **standalone mode**:

```mermaid
flowchart LR
    A("CLI flag") --> B("environment variable") --> C("BeetstreamNext's YAML config file") --> D("Beets' config file<br/><br/>(if you point it at one, and only for <code>library-db</code> and <code>music-root</code>)") --> E("WebUI setting") --> F("Built-in default")

style A fill:#ffffff12,stroke:#808080
style B fill:#ffffff12,stroke:#808080
style C fill:#ffffff12,stroke:#808080
style D fill:#ffffff12,stroke:#808080
style E fill:#ffffff12,stroke:#808080
style F fill:#ffffff12,stroke:#808080
```

For list-based settings (like for example `ip_whitelist`/`ip_blacklist`): pinning is _per individual entry_.

Settings marked <span style="color:#fab915">**requires restart**</span> only take effect after the server is restarted. Settings marked <span style="color:#6b5c58">**standalone only**</span> are not used when running as a Beets plugin.

> [!NOTE]
> BeetstreamNext checks at startup whether the music folder and the beets library database are writable on disk.
> 
> If either isn't (for example when they're bind-mounted `:ro`, as in the [Betanin example stack](./installation.md#example-stack-beetstreamnext--betanin)), the WebUI will show a red "read-only" pill next to any setting that would otherwise need write access."""

badges = {
    'standalone_only': ('#6b5c58', 'standalone only'),
    'sensitive': ('#6b5c58', 'sensitive'),
    'requires_restart': ('#fab915', 'requires restart'),
}


##

def badges_line(descriptor: dict) -> str | None:

    spans = [
        f'<span style="color:{color}">**{label}**</span>'
        for flag, (color, label) in badges.items()
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

    sections = [header]
    for category, heading in CATEGORIES:
        section = render_category(category, heading)
        if section:
            sections.append(section)

    return '\n\n'.join(sections) + '\n'


def main() -> None:

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true', help='just compare with the committed file instead of writing')
    args = parser.parse_args()

    md_content = render()

    if args.check:
        current = OUT_PATH.read_text() if OUT_PATH.exists() else None

        if current != md_content:
            sys.exit(f'{OUT_PATH} is out of date, run: uv run python tools/regen_config_doc.py')
        print(f'{OUT_PATH} is up to date.')

        return

    OUT_PATH.write_text(md_content)
    print(f'Wrote {OUT_PATH}')


if __name__ == '__main__':
    main()
