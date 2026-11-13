import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import os
import tempfile
import unittest
from unittest.mock import patch

from lyric import paths


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='lyric-', dir='/tmp/opencode')
        root = Path(self.directory.name)
        self.legacy_stream = root / 'legacy-stream'
        self.legacy_stream.mkdir()
        self.legacy_sync = root / 'legacy-sync.json'
        self.legacy_sync.write_text('{"[\"A\", \"B\", 30]": 0.25}')
        self.stream = root / 'cache' / 'stream'
        self.sync = root / 'config' / 'sync.json'
        self.fonts = root / 'config' / 'fonts.json'
        self.patches = [
            patch.object(paths, 'LEGACY_STREAM', self.legacy_stream),
            patch.object(paths, 'LEGACY_SYNC', self.legacy_sync),
            patch.object(paths, 'LEGACY_FONTS', root / 'missing-fonts.json'),
            patch.object(paths, 'STREAM_DIR', self.stream),
            patch.object(paths, 'SYNC_FILE', self.sync),
            patch.object(paths, 'FONT_FILE', self.fonts),
            patch.object(paths, 'CONFIG_DIR', root / 'config'),
            patch.object(paths, 'CACHE_DIR', root / 'cache'),
            patch.object(paths, 'STATE_DIR', root / 'state'),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.directory.cleanup()

    def test_copies_caches_and_offsets_once(self):
        cached = self.legacy_stream / 'Artist - Song.wlrc'
        cached.write_text('# timing-parser-v2\n')
        os.utime(cached, (1_000_000, 1_000_000))

        migrated = paths.migrate()

        self.assertEqual(sorted(migrated), ['Artist - Song.wlrc', 'legacy-sync.json'])
        self.assertTrue(self.stream.joinpath('Artist - Song.wlrc').is_file())
        self.assertEqual(self.sync.read_text(), self.legacy_sync.read_text())
        # Freshness checks compare mtimes, so they have to survive the copy.
        self.assertEqual(int(self.stream.joinpath('Artist - Song.wlrc').stat().st_mtime),
                         1_000_000)
        # Second run: nothing left to do, and nothing gets clobbered.
        self.assertEqual(paths.migrate(), [])

    def test_existing_files_are_not_overwritten(self):
        cached = self.legacy_stream / 'Artist - Song.wlrc'
        cached.write_text('old')
        self.stream.mkdir(parents=True)
        self.stream.joinpath('Artist - Song.wlrc').write_text('new')

        paths.migrate()

        self.assertEqual(self.stream.joinpath('Artist - Song.wlrc').read_text(), 'new')

    def test_fonts_are_migrated_but_not_required(self):
        self.assertEqual(paths.migrate(), ['legacy-sync.json'])
        self.assertFalse(self.fonts.exists())

    def test_missing_legacy_layout_is_harmless(self):
        for path in self.legacy_stream.iterdir():
            path.unlink()
        self.legacy_stream.rmdir()
        self.legacy_sync.unlink()
        self.assertEqual(paths.migrate(), [])


class LocationTests(unittest.TestCase):
    def test_paths_are_xdg_shaped(self):
        self.assertTrue(str(paths.STREAM_DIR).endswith('lyric/stream'))
        self.assertTrue(str(paths.SYNC_FILE).endswith('lyric/sync.json'))
        self.assertTrue(str(paths.LOG_FILE).endswith('lyric/lyric.log'))


if __name__ == '__main__':
    unittest.main()
