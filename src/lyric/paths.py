"""XDG locations plus a one-time migration from the pre-rename layout."""
import logging
import os
import shutil
from pathlib import Path

APP = 'lyric'


def _xdg(variable, fallback):
    value = os.environ.get(variable, '')
    return Path(value) if value else Path.home() / fallback


CONFIG_DIR = _xdg('XDG_CONFIG_HOME', '.config') / APP
STATE_DIR = _xdg('XDG_STATE_HOME', '.local/state') / APP
CACHE_DIR = _xdg('XDG_CACHE_HOME', '.cache') / APP

STREAM_DIR = CACHE_DIR / 'stream'
SYNC_FILE = CONFIG_DIR / 'sync.json'
FONT_FILE = CONFIG_DIR / 'fonts.json'
LOG_FILE = STATE_DIR / 'lyric.log'

# Layout used before the project was renamed; migrated once, read-only afterwards.
LEGACY_STREAM = Path.home() / '.local/share/lrc-tools/lyrics/stream'
LEGACY_SYNC = Path.home() / '.config/lrc-tools/sync.json'
LEGACY_FONTS = Path.home() / '.config/lrc-tools/custom_fonts.json'


def ensure_dirs():
    for path in (CONFIG_DIR, STATE_DIR, CACHE_DIR, STREAM_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _copy(source, destination):
    """Copy without clobbering, preserving mtime so cache freshness survives."""
    if not source.is_file() or destination.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return True


def migrate():
    """Bring caches, sync offsets and custom fonts over from the old directory.

    Idempotent: existing files are never overwritten. Preserving mtimes matters
    because ``adapter.fetch`` decides from a cache's mtime whether an old
    line-synced file is still worth re-searching for native word timings.
    """
    ensure_dirs()
    migrated = []
    if LEGACY_STREAM.is_dir():
        for cached in sorted(LEGACY_STREAM.glob('*.wlrc*')):
            if _copy(cached, STREAM_DIR / cached.name):
                migrated.append(cached.name)
    for source, destination in ((LEGACY_SYNC, SYNC_FILE), (LEGACY_FONTS, FONT_FILE)):
        if _copy(source, destination):
            migrated.append(source.name)
    if migrated:
        logging.info('Migrated %d file(s) from %s', len(migrated), LEGACY_STREAM.parent.parent)
    return migrated
