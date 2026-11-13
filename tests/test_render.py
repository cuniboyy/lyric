import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import unittest

from lyric import fonts, palette, render


class RenderTests(unittest.TestCase):
    def setUp(self):
        palette.PALETTE = dict(palette.FALLBACK)

    def test_emits_one_synchronized_frame(self):
        frame = render.render('HELLO', fonts.get('block'), False, 0, '', '')
        self.assertTrue(frame.startswith('\033[?2026h'))
        self.assertTrue(frame.endswith('\033[?2026l'))
        self.assertIn('█', frame)

    def test_sweep_advances_the_sung_colour(self):
        font = fonts.get('block')
        early = render.render('HELLO', font, False, 0, '', '')
        late = render.render('HELLO', font, False, 1, '', '')
        sung = palette.ansi(palette.PALETTE['sung'], palette.FALLBACK['sung'])
        self.assertNotEqual(early, late)
        self.assertGreater(early.count(sung), 1)
        self.assertIn(palette.ansi(palette.PALETTE['upcoming'],
                                   palette.FALLBACK['upcoming']), early)

    def test_unknown_glyphs_fall_back_to_readable_text(self):
        frame = render.render('日本語', fonts.get('block'), False, 0, '', '')
        self.assertIn('日本語', frame)

    def test_plain_mode_uses_regular_text(self):
        frame = render.render('hello there', fonts.get('block'), True, 0, '', '')
        self.assertIn('hello there', frame)
        self.assertNotIn('█', frame)

    def test_footer_and_preview_are_centered(self):
        frame = render.render('HELLO', fonts.get('compact'), False, 0,
                              'next: WORLD', 'native word sync')
        self.assertIn('next: WORLD', frame)
        self.assertIn('native word sync', frame)

    def test_empty_word_still_produces_a_frame(self):
        frame = render.render('', fonts.get('block'), False, 0, '', 'footer')
        self.assertIn('\033[?2026h', frame)
        self.assertIn('footer', frame)

    def test_clip_does_not_split_wide_characters(self):
        self.assertEqual(render.clip('日本語', 4), '日本')
        self.assertEqual(render.clip('abc', 2), 'ab')

    def test_custom_fonts_are_registered(self):
        fonts.register('test', {'A': ['x'], ' ': [' ']})
        self.assertIs(fonts.get('test'), fonts.FONTS['test'])
        self.assertEqual(fonts.render_text('a', 'test'), ['x '])

    def test_unknown_font_falls_back_to_block(self):
        self.assertIs(fonts.get('nope'), fonts.BLOCK_LETTERS)


if __name__ == '__main__':
    unittest.main()
