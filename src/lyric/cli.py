"""Command line entry point."""
import argparse
import asyncio
import logging
import shlex
import subprocess
import sys

from lyric import __version__, adapter, fonts, paths, players


def parse(argv=None):
    parser = argparse.ArgumentParser(
        prog='lyric',
        description='Synchronized lyrics for any MPRIS player, one word at a time.')
    parser.add_argument('--font', default='block', metavar='NAME',
                        help='glyph font to draw with (default: block; custom fonts '
                             'come from ~/.config/lyric/fonts.json)')
    parser.add_argument('--plain', action='store_true', help='regular-sized text')
    parser.add_argument('--offset', type=float, default=0, metavar='SECONDS',
                        help='advance lyrics by this many seconds')
    parser.add_argument('--player', metavar='NAME',
                        help='follow this player instead of the active one '
                             '(name or full org.mpris.MediaPlayer2.* bus name)')
    parser.add_argument('--start-cmd', metavar='COMMAND',
                        help='run this command first when no player is on the bus')
    parser.add_argument('--list-players', action='store_true',
                        help='list players MPRIS can see, then exit')
    parser.add_argument('--fetch', action='store_true',
                        help='fetch lyrics for the current track without opening the UI')
    parser.add_argument('--export', nargs='?', default=None, const='', metavar='PATH',
                        help='write the current lyrics to a shareable .lrc file')
    info = parser.add_mutually_exclusive_group()
    info.add_argument('--minimal', action='store_true', default=True,
                      help='hide the footer (default)')
    info.add_argument('--info', dest='minimal', action='store_false',
                      help='show timing source and keyboard hints')
    parser.add_argument('--version', action='version', version=f'lyric {__version__}')
    return parser.parse_args(argv)


def normalize_player(name):
    """Accept ``spotify`` or the full bus name."""
    if not name:
        return None
    name = name.strip()
    return name if name.startswith(players.PREFIX) else players.PREFIX + name


def start(args):
    """Launch the user's player if nothing is on the bus yet."""
    if not args.start_cmd:
        return
    if asyncio.run(players.any_players()):
        return
    logging.info('Starting player: %s', args.start_cmd)
    try:
        subprocess.Popen(shlex.split(args.start_cmd), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except (OSError, ValueError):
        logging.exception('Could not start %s', args.start_cmd)


def report(rows):
    for row in rows:
        track = f"{row['artist']} — {row['title']}" if row['title'] else ''
        print(f"{row['player']:<24} {row['status']:<8} {row['identity']:<24}"
              f"{'  ' + track if track else ''}")


def main(argv=None):
    args = parse(argv)
    paths.ensure_dirs()
    logging.basicConfig(filename=paths.LOG_FILE, level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    paths.migrate()
    fonts.load(paths.FONT_FILE)
    if args.font not in fonts.FONTS:
        print(f"unknown font '{args.font}'; available: {', '.join(fonts.FONTS)}",
              file=sys.stderr)
        return 2
    args.player = normalize_player(args.player)

    if args.list_players:
        report(asyncio.run(players.describe()))
        return 0

    if args.fetch or args.export is not None:
        info = asyncio.run(players.current(args.player))
        if not info:
            print('Nothing playing (no MPRIS player has a track).', file=sys.stderr)
            return 1
        if args.export is not None:
            return adapter.export(info, args.export)
        result = adapter.fetch(info)
        print(result or 'No synced lyrics available for this track.')
        return 0 if result else 1

    start(args)
    from lyric import ui
    return ui.run(args)


if __name__ == '__main__':
    sys.exit(main())
