"""Independent timed-lyrics sources; a failing provider never aborts the search."""
from dataclasses import dataclass
import difflib
import json
import logging
import queue
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from lyric.timing import events_from_spans


@dataclass
class Result:
    entries: list
    native: bool
    source: str


def get_text(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(request, timeout=8) as response:
        return response.read().decode('utf-8')


def get_json(url):
    return json.loads(get_text(url))


def normalize(text):
    return ''.join(c.casefold() for c in text if c.isalnum())


def matches(info, candidate, clean_title):
    """Reject the wrong artist, a different version, or a different duration."""
    artist, title, duration, _ = info
    other_artist = candidate.get('artist_name', candidate.get('artistName', ''))
    other_title = candidate.get('track_name', candidate.get('trackName', ''))
    if normalize(artist) != normalize(other_artist):
        return False
    versions = r'\b(live|acoustic|demo|remix|cover|karaoke|instrumental|slowed|sped)\b'
    if set(re.findall(versions, title.lower())) != set(re.findall(versions, other_title.lower())):
        return False
    if difflib.SequenceMatcher(None, normalize(clean_title(title)),
                              normalize(clean_title(other_title))).ratio() < .88:
        return False
    other_duration = candidate.get('duration', 0)
    return not duration or bool(other_duration and abs(float(other_duration) - duration) <= 2)


def timestamp(value):
    text = str(value).strip()
    if text.endswith('ms'):
        return float(text[:-2]) / 1000
    if text.endswith('s'):
        return float(text[:-1])
    result = 0
    for part in text.split(':'):
        result = result * 60 + float(part)
    return result


def stamp(value):
    millis = round(value * 1000)
    minutes, remainder = divmod(millis, 60000)
    return f'[{minutes:02}:{remainder / 1000:06.3f}]'


def parse_ttml(text, duration, convert_lrc):
    """Parse word-level TTML, falling back to its lines when spans are coarse."""
    root = ET.fromstring(text)
    body = root.find('.//{*}body')
    if body is not None and body.get('dur') and duration:
        if abs(timestamp(body.get('dur')) - duration) > 2:
            return None
    parents = {child: parent for parent in root.iter() for child in parent}

    def background(element):
        while element is not None:
            if any(key.rsplit('}', 1)[-1] == 'role' and value in ('x-bg', 'background')
                   for key, value in element.attrib.items()):
                return True
            element = parents.get(element)
        return False

    def main_text(element):
        if background(element):
            return ''
        return (element.text or '') + ''.join(main_text(child) + (child.tail or '') for child in element)

    spans, lines, complete = [], [], True
    for phrase in root.findall('.//{*}p'):
        if background(phrase):
            continue
        content = main_text(phrase).strip()
        if not content or not phrase.get('begin'):
            continue
        lines.append(stamp(timestamp(phrase.get('begin'))) + content)
        leaves = [span for span in phrase.findall('.//{*}span')
                  if span.get('begin') and span.get('end') and not span.findall('.//{*}span')
                  and not background(span)]
        if not leaves or any(len(''.join(span.itertext()).strip().split()) > 1 for span in leaves):
            complete = False
            continue
        pending, begin, end = '', None, None
        for span in leaves:
            part = ''.join(span.itertext())
            if not part.strip():
                continue
            if pending and part[0].isspace():
                spans.append((begin, end, pending))
                pending, begin = '', None
            if begin is None:
                begin = timestamp(span.get('begin'))
            end = timestamp(span.get('end'))
            pending += part
            if part[-1].isspace() or (span.tail and span.tail[0].isspace()):
                spans.append((begin, end, pending))
                pending, begin = '', None
        if pending:
            spans.append((begin, end, pending))
    if complete and spans:
        return Result(events_from_spans(spans), True, 'BiniLyrics / lrc.red')
    entries = convert_lrc('\n'.join(lines), duration)
    return Result(entries, False, 'BiniLyrics / lrc.red') if entries else None


def bini(info, convert_lrc, clean_title):
    artist, title, duration, _ = info
    params = urllib.parse.urlencode({'track': clean_title(title), 'artist': artist})
    data = get_json('https://lyrics-api.binimum.org/?' + params)
    candidates = [item for item in data.get('results', []) if matches(info, item, clean_title)]
    candidates.sort(key=lambda item: item.get('timing_type') != 'word')
    fallback = None
    for item in candidates[:3]:
        # Use the documented catalogue URL, not an arbitrary URL from a response.
        isrc = item.get('isrc', '')
        if not re.fullmatch(r'[A-Za-z0-9]{12}', isrc):
            continue
        try:
            result = parse_ttml(get_text(f'https://lrc.red/s/{isrc}.ttml'), duration, convert_lrc)
            if result and result.native:
                return result
            if result and fallback is None:
                fallback = result
        except Exception as error:
            logging.info('BiniLyrics candidate %s: %s', isrc, error)
    return fallback


def lrclib(info, convert_lrc, clean_title):
    artist, title, duration, album = info
    params = {'artist_name': artist, 'track_name': title}
    if duration:
        params['duration'] = round(duration)
    if album:
        params['album_name'] = album
    try:
        data = get_json('https://lrclib.net/api/get?' + urllib.parse.urlencode(params))
        text = data.get('syncedLyrics') or ('[00:00.000]' if data.get('instrumental') else '')
        if text:
            entries = convert_lrc(text, duration)
            if entries:
                return Result(entries, bool(re.search(r'<\d+:\d', text)), 'LRCLIB')
    except Exception as error:
        logging.info('LRCLIB exact lookup: %s', error)
    params = {'artist_name': artist, 'track_name': clean_title(title)}
    data = get_json('https://lrclib.net/api/search?' + urllib.parse.urlencode(params))
    for item in data:
        if matches(info, item, clean_title) and item.get('syncedLyrics'):
            text = item['syncedLyrics']
            entries = convert_lrc(text, duration)
            if entries:
                return Result(entries, bool(re.search(r'<\d+:\d', text)), 'LRCLIB search')
    return None


def synced(provider, info, convert_lrc, clean_title):
    """Use a ``syncedlyrics`` provider that exposes enhanced word timings."""
    from syncedlyrics.providers import Megalobiz, Musixmatch, NetEase
    artist, title, duration, _ = info
    client = {'Musixmatch': lambda: Musixmatch(enhanced=True), 'NetEase': NetEase,
              'Megalobiz': Megalobiz}[provider]()
    # Keep provider warnings in the file logger, not over the karaoke screen.
    for handler in client.logger.handlers[:]:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            client.logger.removeHandler(handler)
            handler.close()
    try:
        lyrics = client.get_lrc(f'{clean_title(title)} {artist}')
        text = lyrics.synced if lyrics and lyrics.synced else ''
    finally:
        client.session.close()
    entries = convert_lrc(text, duration)
    return Result(entries, bool(re.search(r'<\d+:\d', text)), provider) if entries else None


def search_backups(info, convert_lrc, clean_title, timeout=18, native_grace=6):
    """Race independent sources; give native word timing priority over line estimates."""
    jobs = [('BiniLyrics', lambda: bini(info, convert_lrc, clean_title)),
            ('LRCLIB', lambda: lrclib(info, convert_lrc, clean_title))]
    jobs += [(name, lambda name=name: synced(name, info, convert_lrc, clean_title))
             for name in ('Musixmatch', 'NetEase', 'Megalobiz')]
    replies = queue.Queue()

    def worker(name, job):
        try:
            replies.put(job())
        except Exception as error:
            logging.info('Lyrics source %s: %s', name, error)
            replies.put(None)

    for name, job in jobs:
        threading.Thread(target=worker, args=(name, job), daemon=True).start()
    deadline, fallback, pending = time.monotonic() + timeout, None, len(jobs)
    while pending and time.monotonic() < deadline:
        try:
            result = replies.get(timeout=max(.001, deadline - time.monotonic()))
        except queue.Empty:
            break
        pending -= 1
        if not result or not result.entries:
            continue
        if result.native:
            return result
        if fallback is None:
            fallback = result
            deadline = min(deadline, time.monotonic() + native_grace)
    return fallback
