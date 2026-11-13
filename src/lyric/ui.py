"""The karaoke loop: sample the player, resolve the current word, draw it."""
from bisect import bisect_right
import logging
import os
import re
import select
import signal
import sys
import termios
import threading
import time
import tty

from lyric import adapter, fonts, palette, paths, players, render
from lyric.clock import PlaybackClock
from lyric.monitor import Monitor
from lyric.offsets import SyncOffsets

IDLE = 'waiting for a player — start something MPRIS can see'
FRAME_TIME = 1 / 60


def watch(stop, monitor, state):
    """Fetch lyrics for whatever the monitor is following, in the background."""
    attempted = {}
    while not stop.is_set():
        sample = monitor.sample
        info = sample.info if sample else None
        if info and time.monotonic() >= attempted.get(info, 0):
            attempted[info] = time.monotonic() + 120
            try:
                state[info] = 'ready' if adapter.fetch(info) else 'missing'
            except Exception:
                state[info] = 'missing'
                logging.exception('Lyrics fetch failed')
        stop.wait(1)


def segments(info, cached):
    """Parse a cache file into a word timeline, upgrading line-only files."""
    if adapter.ESTIMATED_MARKER in cached:
        return adapter.word_timings(cached, info[2])
    return adapter.parse(cached)


def describe_source(cached):
    source = ('native word sync' if adapter.NATIVE_MARKER in cached else 'estimated word sync')
    provider = re.search(r'^# timing-source: .*\(([^\n]+)\)$', cached, re.M)
    return source + (' · ' + provider[1] if provider else '')


def run(args):
    font = fonts.get(args.font)
    stop = threading.Event()
    state = {}
    monitor = Monitor(stop, args.player).start()
    threading.Thread(target=watch, args=(stop, monitor, state), daemon=True).start()
    offsets = SyncOffsets(os.getenv('LYRIC_SYNC_FILE', str(paths.SYNC_FILE)))
    clock = PlaybackClock()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    signature, entries, starts, previous = None, [], [], None
    last_sample, info, offset, source = None, None, args.offset, ''
    show_info = not args.minimal
    settings = None
    if sys.stdin.isatty():
        settings = termios.tcgetattr(sys.stdin)
        tty.setcbreak(sys.stdin.fileno())
    palette.sync(force=True)
    sys.stdout.write('\033[?1049h\033[?25l\033[2J')
    sys.stdout.flush()
    try:
        while not stop.is_set():
            palette.sync()
            if settings and select.select([sys.stdin], [], [], 0)[0]:
                chunk = os.read(sys.stdin.fileno(), 64).decode(errors='ignore')
                for key in chunk:
                    if key in ('q', '\x1b'):
                        stop.set()
                    elif key == 'i':
                        show_info = not show_info
                    elif key == ' ':
                        # A control action is rare and must not stall animation.
                        threading.Thread(target=players.send_transport,
                                         args=(monitor.target, 'PlayPause'), daemon=True).start()
                    elif key in ('+', '=', '-', '0') and info:
                        offset = 0 if key == '0' else offset + (.05 if key in ('+', '=') else -.05)
                        try:
                            offsets.save(info, offset - args.offset)
                        except OSError:
                            logging.exception('Could not save sync offset')

            sample = monitor.sample
            text, preview, progress = IDLE, '', 0
            if sample:
                if info != sample.info:
                    info = sample.info
                    offset = offsets.get(info) + args.offset
                if sample is not last_sample:
                    clock.position((info, sample.epoch), sample.position, sample.status, sample.when)
                    last_sample = sample
                position = clock.current(time.monotonic()) + offset
                path = adapter.cache_path(info)
                stamp = (info, path.stat().st_mtime_ns if path.exists() else None)
                if stamp != signature:
                    signature = stamp
                    cached = path.read_text() if path.exists() else ''
                    entries = segments(info, cached)
                    starts = [entry[0] for entry in entries]
                    source = describe_source(cached) if cached else ''
                text = 'no synced lyrics found' if state.get(info) == 'missing' else 'fetching lyrics…'
                if entries:
                    index = bisect_right(starts, position) - 1
                    text = entries[index][1] if index >= 0 else ''
                    if index >= 0 and index + 1 < len(entries):
                        span = starts[index + 1] - starts[index]
                        progress = (position - starts[index]) / span if span > 0 else 1
            else:
                info = None

            player = monitor.target.rsplit('.', 1)[-1] if monitor.target else ''
            footer = (f'{player} · {source} | {offset:+.2f}s | '
                      f'+/- sync · 0 reset · space pause · q quit' if show_info else '')
            frame = (text, int(progress * 40), preview, footer, render.size(),
                     tuple(palette.PALETTE[key] for key in palette.FALLBACK))
            if frame != previous:
                sys.stdout.write(render.render(text, font, args.plain, progress, preview, footer))
                sys.stdout.flush()
                previous = frame
            stop.wait(FRAME_TIME)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if settings:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, settings)
        sys.stdout.write('\033[?2026l\033[0m\033[?25h\033[?1049l')
        sys.stdout.flush()
        monitor.thread.join(timeout=1)
    return 0
