import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import unittest

from lyric.timing import clean_text, events_from_spans, normalize_events


class TimingTests(unittest.TestCase):
    def test_simultaneous_words_keep_the_first_vocal_event(self):
        self.assertEqual(normalize_events([(1, 'lead'), (1, 'backing'), (2, 'next')]),
                         [(1, 'lead'), (2, 'next')])

    def test_blank_markers_need_a_real_rest(self):
        # A 20 ms hole is noise, a 150 ms hole is a real pause in the vocal.
        self.assertEqual(normalize_events([(1, 'one'), (1.1, ''), (1.12, 'three')]),
                         [(1, 'one'), (1.12, 'three')])
        self.assertEqual(normalize_events([(1, 'one'), (1.2, ''), (1.4, 'three')]),
                         [(1, 'one'), (1.2, ''), (1.4, 'three')])

    def test_spans_clip_and_keep_silence(self):
        spans = [(1, 1.4, 'hello '), (1.4, 1.6, 'world'), (2.5, 3, 'again')]
        self.assertEqual(events_from_spans(spans),
                         [(1, 'hello'), (1.4, 'world'), (1.6, ''), (2.5, 'again'), (3, '')])

    def test_invalid_entries_are_dropped(self):
        self.assertEqual(events_from_spans([(1, 1, ''), (-1, 2, 'x'), ('a', 2, 'y'),
                                            (2, 1, 'backwards'), (3, 4, 'ok')]),
                         [(3, 'ok'), (4, '')])

    def test_clean_text_strips_escapes_and_controls(self):
        self.assertEqual(clean_text('\x1b[31mhi\x1b[0m\x07'), 'hi')
        self.assertEqual(clean_text('a\u200db'), 'a\u200db')


if __name__ == '__main__':
    unittest.main()
