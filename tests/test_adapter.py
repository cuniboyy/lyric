import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import tempfile
import unittest
from unittest.mock import patch

from lyric import adapter, sources
from lyric.clock import PlaybackClock
from lyric.offsets import SyncOffsets


class ClockTests(unittest.TestCase):
    def test_ignores_microsecond_jitter(self):
        clock = PlaybackClock()
        self.assertAlmostEqual(clock.position('one', 10, 'Playing', 100), 10)
        self.assertAlmostEqual(clock.position('one', 10.000004, 'Playing', 100.6), 10.6)
        self.assertAlmostEqual(clock.position('one', 10.000001, 'Playing', 101.2), 11.2)
        self.assertAlmostEqual(clock.position('one', 12, 'Playing', 102), 12)

    def test_pause_seek_and_track_change(self):
        clock = PlaybackClock()
        clock.position('one', 10, 'Playing', 100)
        self.assertEqual(clock.position('one', 10.5, 'Paused', 101), 10.5)
        self.assertEqual(clock.position('one', 10.5, 'Paused', 102), 10.5)
        self.assertEqual(clock.position('one', 40, 'Playing', 103), 40)
        self.assertEqual(clock.position('two', 0, 'Playing', 104), 0)

    def test_stale_report_never_moves_backwards(self):
        clock = PlaybackClock()
        clock.position('song', 10, 'Playing', 100)
        # A stale report must not yank the display 300 ms backwards.
        self.assertAlmostEqual(clock.position('song', 10.7, 'Playing', 101), 11)
        self.assertAlmostEqual(clock.current(101.05), 11.0375)


class ParseTests(unittest.TestCase):
    def test_native_syllables_join_and_keep_gaps(self):
        data = {'metadata': {'totalDuration': '0:30'}, 'lyrics': [
            {'text': 'hello world', 'syllabus': [
                {'time': 1000, 'duration': 150, 'text': 'hel'},
                {'time': 1150, 'duration': 300, 'text': 'lo '},
                {'time': 2000, 'duration': 600, 'text': 'world'}]}]}
        self.assertEqual(adapter.native_timings(data, 30),
                         [(1, 'hello'), (1.45, ''), (2, 'world'), (2.6, '')])
        self.assertEqual(adapter.native_timings(data, 40), [])

    def test_a_line_timestamp_is_not_word_timing(self):
        data = {'metadata': {'totalDuration': '0:10'}, 'lyrics': [
            {'text': 'two words', 'words': [{'time': 1000, 'duration': 900, 'text': 'two words'}]}]}
        self.assertEqual(adapter.native_timings(data, 10), [])

    def test_background_vocals_are_skipped(self):
        data = {'metadata': {'totalDuration': '0:10'}, 'lyrics': [
            {'text': 'lead', 'isBackground': True,
             'syllabus': [{'time': 500, 'duration': 100, 'text': 'lead'}]},
            {'text': 'solo', 'syllabus': [{'time': 1000, 'duration': 400, 'text': 'solo'}]}]}
        self.assertEqual(adapter.native_timings(data, 10), [(1, 'solo'), (1.4, '')])

    def test_clean_title_preserves_versions(self):
        self.assertEqual(adapter.clean_title('Song - 2024 Remaster'), 'Song')
        self.assertEqual(adapter.clean_title('Song (Live)'), 'Song (Live)')

    def test_parse_blank_and_repeated_timestamps(self):
        self.assertEqual(adapter.parse('[00:01]hi\n[00:02.000]\n[00:03][00:04]bye'),
                         [(1, 'hi'), (2, ''), (3, 'bye'), (4, 'bye')])

    def test_enhanced_timing(self):
        text = '[00:01]<00:01.25>hello <00:02.50>world'
        self.assertEqual(adapter.word_timings(text, 10), [(1.25, 'hello'), (2.5, 'world')])

    def test_word_estimates_clear_instrumental_gaps(self):
        words = adapter.word_timings('[00:02]hello world\n[00:20]again', 30)
        self.assertEqual(words[0], (2, 'hello'))
        self.assertIn((3.6, ''), words)
        self.assertIn((20, 'again'), words)

    def test_group_words_rebuilds_lines_from_a_word_timeline(self):
        entries = [(1, 'one'), (1.2, 'two'), (1.4, 'three'), (5, 'four')]
        self.assertEqual(adapter.group_words(entries),
                         [(1, 'one two three'), (5, 'four')])

    def test_lrc_document_header(self):
        document = adapter.lrc_document(('Artist', 'Title', 61, 'Album'),
                                        [(1.5, 'hello')])
        self.assertIn('[ti:Title]', document)
        self.assertIn('[ar:Artist]', document)
        self.assertIn('[al:Album]', document)
        self.assertIn('[length:01:01]', document)
        self.assertIn('[by:lyric]', document)
        self.assertIn('[00:01.50]hello', document)


class CacheTests(unittest.TestCase):
    def test_fetch_and_cache(self):
        with tempfile.TemporaryDirectory(prefix='lyric-', dir='/tmp/opencode') as directory:
            with patch.object(adapter, 'CACHE', Path(directory)):
                result = sources.Result(adapter.word_timings('[00:01]hello world', 10),
                                        False, 'LRCLIB')
                with patch.object(adapter, 'fetch_native', return_value=[]), \
                     patch.object(sources, 'search_backups', return_value=result):
                    info = ('test/artist', 'test title', 10, '')
                    path = adapter.fetch(info)
                    self.assertTrue(path.exists())
                    self.assertEqual(adapter.parse(path.read_text())[0], (1, 'hello'))
                    self.assertIn('estimated word timestamps', path.read_text())
                    self.assertEqual(adapter.fetch(info), path)

    def test_native_cache_is_not_refetched(self):
        with tempfile.TemporaryDirectory(prefix='lyric-', dir='/tmp/opencode') as directory:
            with patch.object(adapter, 'CACHE', Path(directory)):
                info = ('Artist', 'Song', 30, '')
                adapter.publish(adapter.cache_path(info), [(1, 'hello')], native=True)
                with patch.object(adapter, 'fetch_native', side_effect=AssertionError('network')):
                    self.assertEqual(adapter.fetch(info), adapter.cache_path(info))

    def test_line_cache_upgrades_to_one_word(self):
        with tempfile.TemporaryDirectory(prefix='lyric-', dir='/tmp/opencode') as directory:
            with patch.object(adapter, 'CACHE', Path(directory)):
                info = ('Artist', 'Song', 30, '')
                path = adapter.cache_path(info)
                path.write_text('# adapter-cache-v3\n# timing-source: line timestamps\n'
                                '[00:01.000]hello world\n[00:05.000]one word only\n')
                adapter.fetch(info)
                words = [text for _, text in adapter.parse(path.read_text()) if text]
                self.assertEqual(words, ['hello', 'world', 'one', 'word', 'only'])

    def test_missing_lyrics_returns_none(self):
        with tempfile.TemporaryDirectory(prefix='lyric-', dir='/tmp/opencode') as directory:
            with patch.object(adapter, 'CACHE', Path(directory)):
                with patch.object(adapter, 'fetch_native', return_value=[]), \
                     patch.object(sources, 'search_backups', return_value=None):
                    self.assertIsNone(adapter.fetch(('Nobody', 'Nothing', 10, '')))


class OffsetTests(unittest.TestCase):
    def test_offsets_persist_per_track(self):
        with tempfile.TemporaryDirectory(prefix='lyric-', dir='/tmp/opencode') as directory:
            path = Path(directory) / 'sync.json'
            offsets = SyncOffsets(path)
            one, two = ('Artist', 'One', 30, ''), ('Artist', 'Two', 30, '')
            offsets.save(one, .25)
            loaded = SyncOffsets(path)
            self.assertEqual(loaded.get(one), .25)
            self.assertEqual(loaded.get(two), 0)

    def test_corrupt_offset_file_is_ignored(self):
        with tempfile.TemporaryDirectory(prefix='lyric-', dir='/tmp/opencode') as directory:
            path = Path(directory) / 'sync.json'
            path.write_text('not json')
            self.assertEqual(SyncOffsets(path).values, {})


if __name__ == '__main__':
    unittest.main()
