import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import unittest

from lyric import cli


class ArgumentTests(unittest.TestCase):
    def test_defaults(self):
        args = cli.parse([])
        self.assertEqual(args.font, 'block')
        self.assertEqual(args.offset, 0)
        self.assertFalse(args.plain)
        self.assertIsNone(args.player)
        self.assertIsNone(args.export)
        self.assertIsNone(args.start_cmd)
        self.assertTrue(args.minimal)

    def test_info_turns_off_minimal(self):
        self.assertFalse(cli.parse(['--info']).minimal)

    def test_export_without_a_path_defaults_to_cwd(self):
        self.assertEqual(cli.parse(['--export']).export, '')
        self.assertEqual(cli.parse(['--export', '/tmp/x.lrc']).export, '/tmp/x.lrc')

    def test_long_and_short_player_forms(self):
        self.assertEqual(cli.normalize_player('vlc'), 'org.mpris.MediaPlayer2.vlc')
        self.assertEqual(cli.normalize_player(' org.mpris.MediaPlayer2.vlc '),
                         'org.mpris.MediaPlayer2.vlc')

    def test_version_exits(self):
        with self.assertRaises(SystemExit):
            cli.parse(['--version'])


if __name__ == '__main__':
    unittest.main()
