from __future__ import annotations

import re

from beetsplug.beetstreamnext.utils.text import parse_duration


class Lrc:
    """
    Parsed LRC (or plain) lyrics with line dicts shaped like OpenSubsonic's `line` entries.
    """

    _ts = r'(\d+:\d{1,2}(?:\.\d{1,3})?)'

    LINE_TS_RE = re.compile(rf'\[{_ts}\]')
    LEADING_TS_RE = re.compile(rf'^(?:\s*\[{_ts}\])+')
    WORD_TS_RE = re.compile(rf'<{_ts}>')
    META_RE = re.compile(r'^\s*\[([A-Za-z#]+)\s*:(.*)\]\s*$')

    def __init__(self, text: str | None):

        self.text = text or ''
        self.meta: dict[str, str] = {}
        self.offset = 0                     # in ms
        self.lines: list[dict] = []         # {'value', and 'start' (ms) / 'cue' when present}
        self.synced = False
        self._parse()

    def _parse(self) -> None:
        parsed: list[tuple[list[int], str]] = []

        for raw in self.text.lstrip('﻿').splitlines():

            line = raw.strip()
            if not line:
                continue

            if lead := self.LEADING_TS_RE.match(line):
                stamps = [round(parse_duration(m.group(1)) * 1000) for m in self.LINE_TS_RE.finditer(lead.group(0))]
                parsed.append((stamps, line[lead.end():].strip()))

            elif meta := self.META_RE.match(line):
                key, value = meta.group(1).lower(), meta.group(2).strip()
                if key == 'offset':
                    try:
                        self.offset = int(value)
                    except ValueError:
                        pass
                else:
                    self.meta[key] = value
            else:
                parsed.append(([], line))

        self.synced = any(stamps for stamps, _ in parsed)
        shift = -self.offset

        for stamps, body in parsed:
            if self.synced and not stamps:
                continue    # Every line needs a start in synced lyrics

            value, cues = self._words(body, shift)
            for stamp in stamps or [None]:
                entry = {'value': value}

                if stamp is not None:
                    entry['start'] = max(stamp + shift, 0)
                if cues:
                    entry['cue'] = cues

                self.lines.append(entry)

        if self.synced:
            self.lines.sort(key=lambda ln: ln['start'])

    def _words(self, body: str, shift: int) -> tuple[str, list[dict]]:
        """
        Strip <mm:ss.xx> tags, return text and word cues.
        """

        tags = list(self.WORD_TS_RE.finditer(body))
        if not tags:
            return body, []

        value = body[:tags[0].start()]

        cues = []
        for i, tag in enumerate(tags):
            chunk = body[tag.end():tags[i + 1].start() if i + 1 < len(tags) else len(body)]
            start = len(value.encode())
            value += chunk

            if chunk.strip():
                cue = {
                    'start': max(round(parse_duration(tag.group(1)) * 1000) + shift, 0),
                    'value': chunk,
                    'byteStart': start,
                    'byteEnd': len(value.encode())
                }

                if i + 1 < len(tags):
                    cue['end'] = max(round(parse_duration(tags[i + 1].group(1)) * 1000) + shift, 0)
                cues.append(cue)

        return value, cues

    def structured(self, cues: bool = False) -> list[dict]:
        """Lines for `structuredLyrics` with word cues if requested."""
        if cues:
            return self.lines
        return [{k: v for k, v in ln.items() if k != 'cue'} for ln in self.lines]

    def plain(self) -> str:
        """Text without timestamps or metadata tags."""
        if not self.synced and not self.meta:
            return self.text
        return '\n'.join(ln['value'] for ln in self.lines)
