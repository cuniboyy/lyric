"""Canonical single-word timelines shared by every provider and the renderer."""
import math
import re

MIN_GAP = .075
ANSI = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\))')


def clean_text(text):
    """Strip escape sequences and control characters without touching ZWJ."""
    text = ANSI.sub('', str(text))
    return ''.join(char for char in text if char.isprintable() or char == '\u200d').strip()


def normalize_events(entries):
    """Stable tie resolution; blank markers must not overwrite simultaneous words."""
    valid = []
    for start, text in entries:
        try:
            start = float(start)
        except (TypeError, ValueError):
            continue
        if math.isfinite(start) and start >= 0:
            valid.append((round(start, 3), clean_text(text)))
    groups = {}
    for start, text in sorted(valid, key=lambda item: item[0]):
        # Prefer the first vocal event, not the alphabetically last text.
        if start not in groups or not groups[start]:
            groups[start] = text
    result = list(groups.items())
    return [(start, text) for i, (start, text) in enumerate(result)
            if text or i + 1 == len(result) or result[i + 1][0] - start >= MIN_GAP]


def events_from_spans(spans):
    """Clip overlaps and keep only meaningful rests between words."""
    valid, seen = [], set()
    for start, end, text in spans:
        try:
            start, end, text = float(start), float(end), clean_text(text)
        except (TypeError, ValueError):
            continue
        key = (round(start, 3), text)
        if (not text or not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end <= start or key in seen):
            continue
        valid.append((start, end, text))
        seen.add(key)
    valid.sort(key=lambda item: item[0])
    by_start = {}
    for span in valid:
        by_start.setdefault(round(span[0], 3), span)
    valid = list(by_start.values())
    events = []
    for i, (start, end, text) in enumerate(valid):
        events.append((start, text))
        next_start = valid[i + 1][0] if i + 1 < len(valid) else math.inf
        if next_start - end >= MIN_GAP:
            events.append((end, ''))
    return normalize_events(events)
