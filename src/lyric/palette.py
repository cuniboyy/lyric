"""Live colour palette, optionally driven by matugen so lyrics match the wallpaper."""
import json
import os
from pathlib import Path
import re
import time

DEFAULT_FILE = Path.home() / '.cache/matugen/lyrics.json'
PALETTE_FILE = Path(os.getenv('LYRIC_PALETTE') or os.getenv('LRC_PALETTE')
                    or str(DEFAULT_FILE))
FALLBACK = {'sung': '#dbe4ff', 'upcoming': '#ffffff', 'dim': '#6b7280'}
PALETTE = dict(FALLBACK)

_stamp = None
_checked = 0.0


def ansi(value, fallback):
    """Convert a hex colour to a truecolor foreground escape."""
    digits = str(value).strip().lstrip('#')
    if len(digits) == 3:
        digits = ''.join(char * 2 for char in digits)
    if not re.fullmatch(r'[0-9a-fA-F]{6}', digits):
        digits = str(fallback).strip().lstrip('#')
    red, green, blue = (int(digits[index:index + 2], 16) for index in (0, 2, 4))
    return f'\033[38;2;{red};{green};{blue}m'


def load():
    """Return the palette, or None if the file is absent or mid-write."""
    try:
        data = json.loads(PALETTE_FILE.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {key: data.get(key, fallback) for key, fallback in FALLBACK.items()}


def sync(force=False):
    """Pick up a new matugen palette without restarting the display."""
    global PALETTE, _stamp, _checked
    now = time.monotonic()
    if not force and now - _checked < 1:
        return
    _checked = now
    try:
        stamp = PALETTE_FILE.stat().st_mtime_ns
    except OSError:
        stamp = None
    if stamp is None:
        if _stamp is not None:
            _stamp = None
            PALETTE = dict(FALLBACK)
        return
    if stamp == _stamp and not force:
        return
    loaded = load()
    if loaded is None:
        # Keep the last good palette and retry; matugen may still be writing.
        return
    _stamp = stamp
    PALETTE = loaded


def colors():
    return {key: ansi(PALETTE[key], FALLBACK[key]) for key in FALLBACK}
