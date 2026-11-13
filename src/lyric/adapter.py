"""Fetch, convert and cache word-level lyrics for the current track."""
import json
import logging
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from lyric import paths
from lyric.timing import events_from_spans, normalize_events

CACHE = paths.STREAM_DIR
STAMP = re.compile(r'\[(\d+):(\d+(?:\.\d+)?)\]')
WORD = re.compile(r'<(\d+):(\d+(?:\.\d+)?)>([^<]*)')

CACHE_HEADER = '# lyric-cache-v1'
# Kept from earlier releases: existing caches on disk are recognised by these
# exact markers, so writing them unchanged preserves every cached track.
PARSER_MARKER = '# timing-parser-v2'
NATIVE_MARKER = '# timing-source: native'
ESTIMATED_MARKER = '# timing-source: line timestamps'
BACKUP_MARKER = '# backup-sources-v1'


def cache_path(info):
    name = re.sub(r'[/\x00-\x1f]', '_', f'{info[0]} - {info[1]}')
    return CACHE / (name[:220] + '.wlrc')


def parse(text):
    """Parse a cached word timeline; enhanced tags carry their own timestamps."""
    entries = []
    for line in text.splitlines():
        stamps = list(STAMP.finditer(line))
        if not stamps:
            continue
        body = line[stamps[-1].end():].strip()
        enhanced = list(WORD.finditer(body))
        if enhanced:
            entries.extend((int(w[1]) * 60 + float(w[2]), w[3].strip()) for w in enhanced)
        else:
            entries.extend((int(s[1]) * 60 + float(s[2]), body) for s in stamps)
    return normalize_events(entries)


def word_timings(text, duration):
    """Keep enhanced timestamps; estimate words only for line-synced sources."""
    lines = parse(text)
    if WORD.search(text):
        return lines
    words = []
    for index, (start, body) in enumerate(lines):
        tokens = body.split()
        end = lines[index + 1][0] if index + 1 < len(lines) else duration
        end = min(end if end > start else start + len(tokens) * .5,
                  start + max(1, len(tokens) * .8))
        if not tokens:
            words.append((start, ''))
            continue
        step = (end - start) / len(tokens)
        words.extend((start + i * step, token) for i, token in enumerate(tokens))
        words.append((end, ''))
    return normalize_events(words)


def get_json(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=12) as response:
        return json.load(response)


def clean_title(title):
    # Only remove mastering labels, never live/demo/acoustic/remix versions.
    return re.sub(r'\s*(?:[-–]\s*|\()(?:(?:19|20)\d{2}\s*)?remaster(?:ed)?(?:\s*(?:19|20)\d{2})?\)?\s*$',
                  '', title, flags=re.I).strip()


def seconds(value):
    result = 0
    for part in str(value).split(':'):
        result = result * 60 + float(part)
    return result


def native_timings(data, duration):
    """Join syllables into words while preserving actual start/end timestamps."""
    source_duration = data.get('metadata', {}).get('totalDuration')
    if duration and source_duration and abs(seconds(source_duration) - duration) > 2:
        return []
    spans = []
    for line in data.get('lyrics', []):
        if line.get('isBackground') or line.get('element', {}).get('isBackground'):
            continue
        syllables = [part for part in (line.get('syllabus') or line.get('words') or [])
                     if isinstance(part, dict) and not part.get('isBackground')]
        explicit_words = not line.get('syllabus') and bool(line.get('words'))
        if len(syllables) <= 1 and len(line.get('text', '').split()) > 1:
            # A timestamp on a whole line is not word timing.
            continue
        pending, start, end = '', None, None
        for syllable in syllables:
            text = syllable.get('text', '')
            if not text:
                continue
            begin = float(syllable['time']) / 1000
            finish = begin + float(syllable.get('duration', 0)) / 1000
            if pending and text[0].isspace():
                spans.append((start, end, pending))
                pending, start = '', None
            if start is None:
                start = begin
            pending += text
            end = finish
            if text[-1].isspace() or syllable.get('part') is False or explicit_words:
                spans.append((start, end, pending))
                pending, start = '', None
        if pending:
            spans.append((start, end, pending))
    return events_from_spans(spans)


def fetch_native(info):
    """Primary source: native word/syllable timings."""
    artist, title, duration, album = info
    params = {'title': clean_title(title), 'artist': artist}
    if duration:
        params['duration'] = round(duration)
    data = get_json('https://lyricsplus.binimum.org/v2/lyrics/get?' + urllib.parse.urlencode(params))
    return native_timings(data, duration)


def fetch(info):
    """Return the cache path for a track, refreshing it when necessary.

    Native word timing wins. A cached file that only has estimated timings is
    re-searched immediately; a backup-derived cache is reused for a day.
    """
    path = cache_path(info)
    cached = path.read_text() if path.exists() else ''
    if ESTIMATED_MARKER in cached:
        return publish(path, word_timings(cached, info[2]), native=False, source='cache')
    if PARSER_MARKER in cached and (NATIVE_MARKER in cached or
            BACKUP_MARKER in cached and time.time() - path.stat().st_mtime < 86400):
        return path
    entries = []
    try:
        entries = fetch_native(info)
    except Exception as error:
        logging.info('Native word source %s - %s: %s', info[0], info[1], error)
    if entries:
        backup = path.with_suffix('.wlrc.before-native')
        if cached and not backup.exists():
            backup.write_text(cached)
        return publish(path, entries, native=True)
    from lyric.sources import search_backups
    result = search_backups(info, word_timings, clean_title)
    if not result:
        logging.info('No synced lyrics: %s - %s', info[0], info[1])
        return path if cached else None
    backup = path.with_suffix('.wlrc.before-native')
    if cached and not backup.exists():
        backup.write_text(cached)
    return publish(path, result.entries, native=result.native, source=result.source)


def publish(path, entries, native, source='LyricsPlus'):
    """Atomically replace a cache file so the display never reads a partial one."""
    CACHE.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=CACHE, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as output:
            output.write(CACHE_HEADER + '\n')
            output.write(PARSER_MARKER + '\n')
            if source != 'cache':
                output.write(BACKUP_MARKER + '\n')
            output.write('# timing-source: ' + ('native word timestamps' if native
                                               else 'estimated word timestamps from lines') + f' ({source})\n')
            for timestamp, word in normalize_events(entries):
                millis = round(timestamp * 1000)
                minutes, remainder = divmod(millis, 60000)
                output.write(f'[{minutes:02}:{remainder / 1000:06.3f}]{word}\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    logging.info('Saved %s', path.name)
    return path


def lines_of(text):
    """Line-level (start, body) pairs; enhanced word tags fold back into the line text."""
    lines = []
    for raw in text.splitlines():
        stamps = list(STAMP.finditer(raw))
        if not stamps:
            continue
        body = WORD.sub('', raw[stamps[-1].end():]).strip()
        start = int(stamps[0].group(1)) * 60 + float(stamps[0].group(2))
        lines.append((start, ' '.join(body.split())))
    return lines


def lrclib_lines(info):
    """Real line breaks are only recoverable from a line-synced source, so ask one."""
    from lyric.sources import matches, normalize
    artist, title, duration, album = info
    params = {'artist_name': artist, 'track_name': title}
    if duration:
        params['duration'] = round(duration)
    if album:
        params['album_name'] = album
    try:
        data = get_json('https://lrclib.net/api/get?' + urllib.parse.urlencode(params))
        lines = lines_of(data.get('syncedLyrics') or '')
        if lines:
            return lines
    except Exception as error:
        logging.info('Export lookup (exact): %s', error)
    data = get_json('https://lrclib.net/api/search?artist_name='
                    + urllib.parse.quote(artist) + '&track_name=' + urllib.parse.quote(title))
    same = [item for item in (data if isinstance(data, list) else [])
            if item.get('syncedLyrics')
            and normalize(item.get('artistName', '')) == normalize(artist)
            and normalize(item.get('trackName', '')) == normalize(title)]
    for item in same:
        if matches(info, item, clean_title):
            return lines_of(item['syncedLyrics'])
    for item in same:
        lines = lines_of(item['syncedLyrics'])
        if lines:
            return lines
    return []


def group_words(entries):
    """Rebuild lines from a word timeline when no line-synced source is reachable."""
    valid = [(start, word) for start, word in entries if word]
    gaps = [b[0] - a[0] for a, b in zip(valid, valid[1:]) if b[0] > a[0]]
    threshold = max(.35, sorted(gaps)[len(gaps) // 2] * 1.9) if gaps else .35
    lines, words, start, last = [], [], None, 0
    for timestamp, word in entries:
        if not word:
            continue
        if start is None:
            start = timestamp
        elif timestamp - last >= threshold or timestamp - start > 7:
            lines.append((start, ' '.join(words)))
            words, start = [], timestamp
        words.append(word)
        last = timestamp
    if words:
        lines.append((start, ' '.join(words)))
    return lines


def lrc_document(info, lines):
    artist, title, duration, album = info
    minutes, remainder = divmod(round(duration), 60)
    header = [f'[ti:{title}]', f'[ar:{artist}]']
    if album:
        header.append(f'[al:{album}]')
    if duration:
        header.append(f'[length:{minutes:02}:{remainder:02}]')
    header.append('[by:lyric]')
    body = []
    for start, text in lines:
        millis = round(start * 1000)
        line_minutes, line_remainder = divmod(millis, 60000)
        body.append(f'[{line_minutes:02}:{line_remainder / 1000:05.2f}]{text}')
    return '\n'.join(header + body) + '\n'


def export(info, destination):
    """Write shareable line-synced .lrc text for the current track."""
    lines = []
    try:
        lines = lrclib_lines(info)
    except Exception as error:
        logging.info('Export lookup failed: %s', error)
    if not lines:
        cached = cache_path(info)
        if cached.exists():
            lines = group_words(parse(cached.read_text()))
    if not lines:
        print('No timed lyrics found for this track.', file=sys.stderr)
        return 1
    artist, title = info[0], info[1]
    name = re.sub(r'[/\x00-\x1f]', '_', f'{artist} - {title}')
    if destination in ('', '-'):
        destination = Path.cwd() / (name[:200] + '.lrc')
    path = Path(destination).expanduser()
    if path.is_dir():
        path = path / (name[:200] + '.lrc')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(lrc_document(info, lines))
    print(path)
    return 0
