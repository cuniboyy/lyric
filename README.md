# lyric

Synchronized lyrics in your terminal — one word at a time, in a giant block
font that fills the screen. Works with **any player that speaks MPRIS**.

```
              ██   ██ ███████ ██     ██     ██████
              ██   ██ ██      ██     ██     ██  ██
              ███████ █████   ██     ██     ██  ██
              ██   ██ ██      ██     ██     ██  ██
              ██   ██ ███████ ██████ ██████ ██████
```

## How it works

`lyric` talks to the session bus directly and follows whatever is actually
playing: a freshly started player takes over immediately, and a tie keeps the
current one so the display never bounces between two players. Position is
sampled over a persistent D-Bus connection and interpolated into a smooth
clock, so 80 ms polling produces a 60 fps sweep without hammering the player.

Lyrics are fetched for the current track in the background and cached. Native
word-level timing is preferred; when a source only has line timestamps, words
are distributed inside the line and the footer labels the result
`estimated word sync`.

## Requirements

- Linux with a session D-Bus (Wayland or X11)
- A player exposing `org.mpris.MediaPlayer2.*`: Spotify, VLC, Strawberry,
  Rhythmbox, mpv (with `mpv-mpris`), Firefox and Chromium media sessions,
  cmus, Sonora, and most others
- Python 3.10+
- A terminal with truecolour and synchronized-update support (Kitty, Alacritty,
  WezTerm, foot, GNOME Terminal, Konsole — anything modern)

The lyrics sources are network services, so an internet connection is needed
the first time a track is played.

## Install

```sh
pipx install git+https://github.com/cuniboyy/lyric
# or
uv tool install git+https://github.com/cuniboyy/lyric
```

From a checkout:

```sh
git clone https://github.com/cuniboyy/lyric
cd lyric
python -m venv .venv
.venv/bin/pip install -e .
```

## Usage

```sh
lyric                    # follow whatever is playing
lyric --player spotify   # follow one player
lyric --info             # show the footer with source, offset and keys
lyric --font compact     # smaller glyphs for small terminals
lyric --plain            # regular-sized text, no block font
lyric --offset 0.3       # nudge lyrics forward 300 ms
lyric --list-players     # what can MPRIS see right now?
lyric --fetch            # fetch lyrics for the current track, no UI
lyric --export           # write the current lyrics to a shareable .lrc
lyric --start-cmd 'flatpak run org.example.Player'
```

### Keys

| key | action |
| --- | --- |
| `q` / `Esc` / `Ctrl+C` | quit and restore the terminal |
| `i` | toggle the footer |
| `space` | play / pause the player |
| `+` / `=` | advance lyrics 50 ms |
| `-` | delay lyrics 50 ms |
| `0` | reset the offset |

Offsets are saved per track and version, so a correction only has to be made
once.

### Player selection

Without `--player`, the most active player wins: `Playing` beats `Paused` beats
`Stopped`, and the current player is kept on a tie. When `playerctld` is
running its recency ordering is used; otherwise the players on the bus are
ranked directly.

`--player` pins one player and accepts either the short name (`spotify`,
`vlc`, `firefox`) or the full bus name (`org.mpris.MediaPlayer2.spotify`).

## Where the lyrics come from

1. **LyricsPlus** — native word/syllable timestamps, joined into words with
   their real start and end times.
2. If that fails, independent backups are raced with a deadline for each:
   - **BiniLyrics / lrc.red** — TTML with word spans
   - **LRCLIB** — exact metadata lookup, then a validated search
   - **Musixmatch**, **NetEase**, **Megalobiz** — via `syncedlyrics`

Native word timing always wins over an estimate, so a slow native source gets
a short grace period before a line-synced result is accepted. Candidates are
checked against artist, title, version tags (live / acoustic / remix / demo …)
and duration (2 second tolerance), so a live version never overwrites the
studio track. Only timed lyrics are accepted; plain text is never displayed as
if it were synced.

## Files

| path | contents |
| --- | --- |
| `~/.cache/lyric/stream/*.wlrc` | word timelines, safe to delete |
| `~/.config/lyric/sync.json` | per-track sync offsets |
| `~/.config/lyric/fonts.json` | optional custom glyph fonts |
| `~/.local/state/lyric/lyric.log` | what was fetched and from where |

All of it follows `XDG_CONFIG_HOME`, `XDG_CACHE_HOME` and `XDG_STATE_HOME`.

If you were running the pre-rename `lrc-tools` layout
(`~/.local/share/lrc-tools`), cached lyrics, sync offsets and custom fonts are
copied over once on first run, preserving cache timestamps. Nothing is deleted.

## Colours

The sung word, the upcoming word and the footer use three colours:

- `LYRIC_PALETTE` — path to a JSON file, default
  `~/.cache/matugen/lyrics.json`
- keys `sung`, `upcoming`, `dim` as hex strings

The file is re-read when it changes, so a wallpaper recolour applies live. It
is meant to be written by a [matugen](https://github.com/InioX/matugen)
template; without the file a blue-white-grey palette is used.

```json
{"sung": "#dbe4ff", "upcoming": "#ffffff", "dim": "#6b7280"}
```

## Fonts

`--font` takes `block` (default), `compact`, or any font defined in
`~/.config/lyric/fonts.json`:

```json
{
  "_format": "a font maps each character to its glyph rows",
  "mini": {"A": ["▄▀▄", "█▀█", "▀ ▀"], " ": ["   ", "   ", "   "]}
}
```

Keys starting with `_` are ignored, so the file can document itself.

## Development

```sh
python -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/python -m unittest discover -s tests -v
```

The end-to-end test drives a fake MPRIS player through a pty and decodes the
screen with `pyte`. It needs the system `python3-dbus`/`python3-gobject`
packages for the fake player:

```sh
LYRIC_E2E=1 .venv/bin/python -m unittest discover -s tests -v
```

## Limitations

- One word at a time is the whole point; there is no scrolling multi-line mode.
- Line-only sources cannot know where a word actually lands inside the line.
- Right-to-left scripts and very wide glyphs are clipped by cell width rather
  than shaped.
- Streaming players report position coarsely (often ~1 s), which the clock
  smooths but cannot make exact.

## Credits

- Block and compact glyph fonts come from
  [tacos-terminal-lyrics](https://github.com/Astropilot/tacos-terminal-lyrics).
- Lyrics from LyricsPlus/BiniLyrics/lrc.red, [LRCLIB](https://lrclib.net),
  Musixmatch, NetEase and Megalobiz, through
  [`syncedlyrics`](https://github.com/rtcq/syncedlyrics).
- D-Bus plumbing via [`dbus-next`](https://github.com/altdesktop/python-dbus-next).

## License

MIT.
