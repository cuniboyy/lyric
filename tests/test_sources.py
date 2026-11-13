import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from lyric import adapter, sources


class SourceTests(unittest.TestCase):
    info = ('Weezer', 'Undone - The Sweater Song - 2024 Remaster', 305, '')

    def test_reject_wrong_artist_version_and_duration(self):
        candidate = {'artist_name': 'Weezer', 'track_name': 'Undone - The Sweater Song',
                     'duration': 305}
        self.assertTrue(sources.matches(self.info, candidate, adapter.clean_title))
        for change in ({'artist_name': 'Cover Band'}, {'duration': 310},
                       {'track_name': 'Undone - The Sweater Song (Live)'}):
            self.assertFalse(sources.matches(self.info, dict(candidate, **change),
                                             adapter.clean_title))

    def test_ttml_joins_syllables_and_preserves_silence(self):
        text = '<tt xmlns="http://www.w3.org/ns/ttml"><body dur="30s"><div>' \
               '<p begin="1s" end="4s"><span begin="1s" end="1.2s">hel</span>' \
               '<span begin="1.2s" end="1.5s">lo</span> ' \
               '<span begin="2s" end="3s">world</span></p></div></body></tt>'
        result = sources.parse_ttml(text, 30, adapter.word_timings)
        self.assertTrue(result.native)
        self.assertEqual(result.entries, [(1, 'hello'), (1.5, ''), (2, 'world'), (3, '')])
        self.assertIsNone(sources.parse_ttml(text, 40, adapter.word_timings))

    def test_line_ttml_stays_one_word(self):
        text = '<tt><body><div><p begin="1" end="4">hello world</p></div></body></tt>'
        result = sources.parse_ttml(text, 30, adapter.word_timings)
        self.assertFalse(result.native)
        self.assertEqual([word for _, word in result.entries if word], ['hello', 'world'])

    def test_background_ttml_is_skipped(self):
        text = '<tt xmlns:ttm="http://www.w3.org/ns/ttml#metadata"><body><div>' \
               '<p begin="1" ttm:role="x-bg"><span begin="1" end="2">ah</span></p>' \
               '<p begin="3"><span begin="3" end="4">lead</span></p></div></body></tt>'
        result = sources.parse_ttml(text, 30, adapter.word_timings)
        self.assertEqual([word for _, word in result.entries if word], ['lead'])

    def test_lrclib_search_after_exact_failure(self):
        data = [{'artistName': 'Weezer', 'trackName': 'Undone - The Sweater Song',
                 'duration': 305, 'syncedLyrics': '[00:01]hello world'}]
        with patch.object(sources, 'get_json', side_effect=[OSError('offline'), data]) as request:
            result = sources.lrclib(self.info, adapter.word_timings, adapter.clean_title)
        self.assertEqual(result.source, 'LRCLIB search')
        self.assertEqual(request.call_count, 2)

    def test_instrumental_marks_a_single_timestamp(self):
        with patch.object(sources, 'get_json',
                          return_value={'instrumental': True, 'syncedLyrics': None}):
            result = sources.lrclib(self.info, adapter.word_timings, adapter.clean_title)
        self.assertEqual(result.entries, [(0, '')])

    def test_backup_race_prefers_native(self):
        native = sources.Result([(1, 'native')], True, 'BiniLyrics')
        line = sources.Result([(1, 'estimated')], False, 'LRCLIB')
        with patch.object(sources, 'bini', return_value=native), \
             patch.object(sources, 'lrclib', return_value=line), \
             patch.object(sources, 'synced', return_value=None):
            result = sources.search_backups(self.info, adapter.word_timings,
                                            adapter.clean_title, timeout=.5)
        self.assertTrue(result.native)

    def test_failure_does_not_block_other_sources(self):
        line = sources.Result([(1, 'hello')], False, 'NetEase')
        with patch.object(sources, 'bini', side_effect=OSError('offline')), \
             patch.object(sources, 'lrclib', return_value=None), \
             patch.object(sources, 'synced',
                          side_effect=lambda name, *_: line if name == 'NetEase' else None):
            result = sources.search_backups(self.info, adapter.word_timings,
                                            adapter.clean_title, timeout=.5)
        self.assertEqual(result.source, 'NetEase')

    def test_stalled_sources_are_time_bounded(self):
        release = threading.Event()

        def stalled(*args):
            release.wait(2)
        try:
            with patch.object(sources, 'bini', side_effect=stalled), \
                 patch.object(sources, 'lrclib', side_effect=stalled), \
                 patch.object(sources, 'synced', side_effect=stalled):
                start = time.monotonic()
                result = sources.search_backups(self.info, adapter.word_timings,
                                                adapter.clean_title, timeout=.03)
                self.assertIsNone(result)
                self.assertLess(time.monotonic() - start, .5)
        finally:
            release.set()

    def test_musixmatch_requests_enhanced_lyrics(self):
        client = MagicMock()
        client.logger.handlers = []
        client.get_lrc.return_value.synced = '[00:01]<00:01.2>hello <00:01.8>world'
        with patch('syncedlyrics.providers.Musixmatch', return_value=client) as factory:
            result = sources.synced('Musixmatch', self.info, adapter.word_timings,
                                    adapter.clean_title)
        factory.assert_called_once_with(enhanced=True)
        self.assertTrue(result.native)
        client.session.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
