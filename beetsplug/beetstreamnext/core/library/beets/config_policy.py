from __future__ import annotations



## Code/command execution (config editor)

# Plugins that run arbitrary code or shell commands straight from the config
CODE_EXECUTION_PLUGINS: frozenset[str] = frozenset({
    'inline',
    'hook'
})

# Top-level keys that can load or evaluate code
CODE_EXECUTION_KEYS: frozenset[str] = frozenset({
    'pluginpath',       # imports Python from arbitrary dirs
    'item_fields',      # Python snippets ran by the inline plugin
    'album_fields'      # Python snippets ran by the inline plugin
})

# Plugin options with a command line / binary to execute
# {plugin: options}
COMMAND_OPTIONS: dict[str, frozenset[str]] = {
    'hook': frozenset({'hooks'}),
    'convert': frozenset({'command', 'formats'}),
    'play': frozenset({'command', 'raw'}),
    'badfiles': frozenset({'commands'}),
    'replaygain': frozenset({'command'}),
    'keyfinder': frozenset({'bin'}),
    'absubmit': frozenset({'extractor'}),
}


# Disk writes (import safety)

# Plugins that modify the music folder when importing
# {plugin: option that enables it}
DISK_WRITING_PLUGINS: dict[str, str | None] = {
    'scrub': 'auto',
    'embedart': 'auto',
    'fetchart': 'auto',
    'hook': None,       # plugin always considered unsafe
}
# TODO: add more?
