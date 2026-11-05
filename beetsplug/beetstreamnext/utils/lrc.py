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
        self.lines: list[dict] = []         # {'value', and 'start' (ms) when synced}
        self._cues: list[list[dict]] = []   # Word cues parallel to `lines` (for synced lyrics)
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

        pairs: list[tuple[dict, list[dict]]] = []

        for stamps, body in parsed:
            if self.synced and not stamps:
                continue    # Every line needs a start in synced lyrics

            value, cues = self._words(body, shift)
            for stamp in stamps or [None]:
                entry = {'value': value}
                line_cues = []

                if stamp is not None:
                    entry['start'] = max(stamp + shift, 0)

                    # Word times are absolute, so for repeated lines they are moved to each repetition
                    delta = stamp - stamps[0]
                    for cue in cues:
                        moved = dict(cue, start=cue['start'] + delta)
                        if 'end' in cue:
                            moved['end'] = cue['end'] + delta
                        line_cues.append(moved)

                pairs.append((entry, line_cues))

        if self.synced:
            pairs.sort(key=lambda p: p[0]['start'])

        self.lines = [entry for entry, _ in pairs]
        self._cues = [cues for _, cues in pairs]

        self._complete_cue_ends()

    def _complete_cue_ends(self) -> None:
        """
        Make cue end times consistent.
        """

        for i, cues in enumerate(self._cues):
            if not cues:
                continue

            if 'end' not in cues[-1] and i + 1 < len(self.lines):
                following = self.lines[i + 1]['start']
                if following >= cues[-1]['start']:
                    cues[-1]['end'] = following

            if any('end' not in c for c in cues):
                for c in cues:
                    c.pop('end', None)
                continue

            for c, nxt in zip(cues, cues[1:] + [None]):
                end = min(c['end'], nxt['start']) if nxt else c['end']
                c['end'] = max(end, c['start'])

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
            text = chunk.strip()

            # Byte offsets of the word in the line (UTF-8 encoded)
            start = len(value.encode()) + len(chunk[:len(chunk) - len(chunk.lstrip())].encode())
            value += chunk

            if text:
                cue = {
                    'start': max(round(parse_duration(tag.group(1)) * 1000) + shift, 0),
                    'value': text,
                    'byteStart': start,
                    'byteEnd': start + len(text.encode()) - 1
                }

                if i + 1 < len(tags):
                    cue['end'] = max(round(parse_duration(tags[i + 1].group(1)) * 1000) + shift, 0)
                cues.append(cue)

        return value, cues

    def structured(self) -> list[dict]:
        """Lines for `structuredLyrics`."""
        return [dict(ln) for ln in self.lines]

    def cue_lines(self) -> list[dict]:
        """Word-level `cueLine` entries (OpenSubsonic songLyrics v2). Empty for non synced lyrics."""
        out = []
        for index, (line, cues) in enumerate(zip(self.lines, self._cues)):
            if not cues:
                continue
            cue_line = {'index': index, 'start': cues[0]['start'], 'value': line['value'], 'cue': cues}
            if 'end' in cues[-1]:
                cue_line['end'] = cues[-1]['end']
            out.append(cue_line)
        return out

    def plain(self) -> str:
        """Text without timestamps or metadata tags."""
        if not self.synced and not self.meta:
            return self.text
        return '\n'.join(ln['value'] for ln in self.lines)
