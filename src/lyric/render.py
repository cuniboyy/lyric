"""Full-screen terminal rendering: one big word, swept as it is sung."""
import os

from wcwidth import wcswidth

from lyric import palette
from lyric.timing import clean_text


def size():
    """Terminal (columns, rows), with a sane fallback when there is no tty."""
    try:
        dimensions = os.get_terminal_size()
        return max(1, dimensions.columns), max(1, dimensions.lines)
    except OSError:
        return 80, 24


def clip(value, width):
    """Truncate without splitting a wide glyph."""
    result = ''
    for char in clean_text(value):
        candidate = result + char
        if wcswidth(candidate) > width:
            break
        result = candidate
    return result


def render(text, font, plain, progress, preview, footer):
    """Draw one word in the block font, filled left-to-right as it is sung.

    Returns a single string of absolute cursor moves and two synchronized
    updates: modern terminals then show the frame atomically instead of
    scanning it, which is what stops half-old/half-new words from appearing.
    """
    cols, rows = size()
    limit = cols - 1

    def centered(value):
        value = clip(value, limit)
        return ' ' * max(0, (limit - wcswidth(value)) // 2) + value

    text = clean_text(text)
    upper = text.upper()
    glyphs = []
    if text and not plain and all(char in font for char in upper):
        height = max(len(font[char]) for char in upper)
        glyphs = [''.join((font[char][i] if i < len(font[char]) else ' ' * len(font[char][0])) + ' '
                          for char in upper).rstrip() for i in range(height)]
        if height > rows or max(map(wcswidth, glyphs), default=0) > limit:
            glyphs = []
    raw = glyphs or ([clip(text, limit)] if text else [])
    width = max(map(wcswidth, raw), default=0)
    left, top = max(0, (limit - width) // 2), max(0, (rows - len(raw)) // 2)
    lines = [''] * rows
    for i, line in enumerate(raw):
        lines[top + i] = ' ' * left + line

    split = left + round(width * max(0, min(1, progress)))
    colors = palette.colors()
    colored = []
    for line in lines:
        prefix = ''
        for char in line:
            if wcswidth(prefix + char) > split:
                break
            prefix += char
        colored.append(colors['sung'] + prefix + colors['upcoming'] + line[len(prefix):] + '\033[0m')
    if preview and rows > 12:
        row = min(rows - 3, rows // 2 + 5)
        colored[row] = colors['dim'] + centered(preview) + '\033[0m'
    if footer and rows > 3:
        colored[-1] = colors['dim'] + centered(footer) + '\033[0m'
    return ('\033[?2026h' + ''.join(f'\033[{i + 1};1H\033[2K{line}'
                                    for i, line in enumerate(colored)) + '\033[?2026l')
