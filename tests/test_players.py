import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import unittest

from lyric import players
from lyric.cli import normalize_player


class Variant:
    """Stand-in for dbus-next's variant wrapper."""

    def __init__(self, value):
        self.value = value


class ChooseTests(unittest.TestCase):
    def test_playing_beats_paused(self):
        states = {'org.mpris.MediaPlayer2.one': 'Paused',
                  'org.mpris.MediaPlayer2.two': 'Playing'}
        self.assertEqual(players.choose('org.mpris.MediaPlayer2.one', states),
                         'org.mpris.MediaPlayer2.two')

    def test_tie_keeps_the_current_player(self):
        states = {'org.mpris.MediaPlayer2.one': 'Playing',
                  'org.mpris.MediaPlayer2.two': 'Playing'}
        self.assertEqual(players.choose('org.mpris.MediaPlayer2.two', states),
                         'org.mpris.MediaPlayer2.two')
        self.assertEqual(players.choose('org.mpris.MediaPlayer2.one', states),
                         'org.mpris.MediaPlayer2.one')

    def test_disappeared_player_is_replaced(self):
        states = {'org.mpris.MediaPlayer2.two': 'Paused'}
        self.assertEqual(players.choose('org.mpris.MediaPlayer2.one', states),
                         'org.mpris.MediaPlayer2.two')

    def test_paused_handoff_waits_for_nothing(self):
        # A paused player must yield the moment something starts playing.
        states = {'org.mpris.MediaPlayer2.paused': 'Paused',
                  'org.mpris.MediaPlayer2.fresh': 'Playing'}
        self.assertEqual(players.choose('org.mpris.MediaPlayer2.paused', states),
                         'org.mpris.MediaPlayer2.fresh')

    def test_any_player_beats_nothing(self):
        states = {'org.mpris.MediaPlayer2.only': 'Stopped'}
        self.assertEqual(players.choose(None, states), 'org.mpris.MediaPlayer2.only')

    def test_no_players(self):
        self.assertIsNone(players.choose('org.mpris.MediaPlayer2.one', {}))

    def test_unresponsive_ranks_below_stopped(self):
        self.assertEqual(players.rank('Playing'), 3)
        self.assertEqual(players.rank('Paused'), 2)
        self.assertEqual(players.rank('Stopped'), 1)
        self.assertEqual(players.rank('Banana'), 0)
        self.assertGreater(players.rank('Stopped'), players.rank(None))


class MetadataTests(unittest.TestCase):
    def test_decodes_a_track(self):
        value = {
            'xesam:title': Variant('Song'),
            'xesam:artist': [Variant('One'), Variant('Two')],
            'xesam:album': Variant('Album'),
            'mpris:length': Variant(180_500_000),
        }
        self.assertEqual(players.decode_metadata(value),
                         {'artist': 'One, Two', 'title': 'Song',
                          'duration': 180.5, 'album': 'Album'})

    def test_untitled_metadata_is_none(self):
        self.assertIsNone(players.decode_metadata({'xesam:title': Variant('')}))
        self.assertIsNone(players.decode_metadata(None))

    def test_missing_length_is_zero(self):
        self.assertEqual(players.decode_metadata({'xesam:title': Variant('Song')})['duration'], 0)

    def test_plain_unwraps_variants(self):
        self.assertEqual(players.plain(Variant(7)), 7)
        self.assertEqual(players.plain(7), 7)

    def test_dictionary_unwrapping(self):
        self.assertEqual(players.unwrap_dictionary({'a': Variant(1)}), {'a': 1})


class PlayerNameTests(unittest.TestCase):
    def test_short_name_is_expanded(self):
        self.assertEqual(normalize_player('spotify'), 'org.mpris.MediaPlayer2.spotify')

    def test_full_name_is_kept(self):
        self.assertEqual(normalize_player('org.mpris.MediaPlayer2.vlc'),
                         'org.mpris.MediaPlayer2.vlc')
        self.assertIsNone(normalize_player(None))


if __name__ == '__main__':
    unittest.main()
