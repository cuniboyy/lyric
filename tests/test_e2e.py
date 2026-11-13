"""End-to-end test: a real pty, a real D-Bus player, and the real UI.

Opt in with LYRIC_E2E=1. The fake player needs the system python3-dbus and
python3-gobject packages, so this is skipped rather than failed when they are
missing.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / 'src'))

import fcntl
import os
import pty
import select
import struct
import subprocess
import tempfile
import termios
import time
import unittest


FAKE = HERE / 'fake_mpris.py'
SYSTEM_PYTHON = '/usr/bin/python3'
PLAYER = 'fakeplayer'
SERVICE = f'org.mpris.MediaPlayer2.{PLAYER}'
COLS, ROWS = 100, 24

FIXTURE = ('# lyric-cache-v1\n'
           '# timing-parser-v2\n'
           '# backup-sources-v1\n'
           '# timing-source: native word timestamps (test fixture)\n'
           '[00:01.000]HELLO\n'
           '[00:05.000]WORLD\n'
           '[00:10.000]\n')


def available():
    try:
        return subprocess.run([SYSTEM_PYTHON, '-c', 'import dbus, gi'],
                              capture_output=True).returncode == 0
    except OSError:
        return False


ENABLED = os.environ.get('LYRIC_E2E') == '1' and available()


@unittest.skipUnless(ENABLED, 'set LYRIC_E2E=1 (needs system python3-dbus) to run')
class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fake = subprocess.Popen(
            [SYSTEM_PYTHON, str(FAKE), '--file', '/nonexistent.mp3',
             '--artist', 'Adapter Test', '--title', 'Render Test',
             '--duration', '30', '--paused', '--start', '2'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        banner = cls.fake.stdout.readline()
        if 'up' not in banner:
            cls.fake.kill()
            raise unittest.SkipTest(f'fake player did not start: {banner!r}')
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if subprocess.run(['dbus-send', '--session', '--print-reply', f'--dest={SERVICE}',
                               '/org/mpris/MediaPlayer2', 'org.freedesktop.DBus.Properties.Get',
                               'string:org.mpris.MediaPlayer2.Player', 'string:PlaybackStatus'],
                              capture_output=True).returncode == 0:
                return
            time.sleep(.1)
        raise unittest.SkipTest('fake player never appeared on the bus')

    @classmethod
    def tearDownClass(cls):
        cls.fake.terminate()
        try:
            cls.fake.wait(timeout=5)
        finally:
            cls.fake.stdout.close()
            cls.fake.stderr.close()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='lyric-e2e-', dir='/tmp/opencode')
        self.root = Path(self.directory.name)
        self.env = dict(os.environ,
                        XDG_CACHE_HOME=str(self.root / 'cache'),
                        XDG_CONFIG_HOME=str(self.root / 'config'),
                        XDG_STATE_HOME=str(self.root / 'state'),
                        PYTHONPATH=str(REPO / 'src'))
        self.cache = self.root / 'cache/lyric/stream'
        self.cache.mkdir(parents=True)
        (self.cache / 'Adapter Test - Render Test.wlrc').write_text(FIXTURE)
        self.seek(2)

    def tearDown(self):
        self.directory.cleanup()

    def seek(self, seconds):
        result = subprocess.run(
            ['dbus-send', '--session', '--print-reply', f'--dest={SERVICE}',
             '/org/mpris/MediaPlayer2', 'org.mpris.MediaPlayer2.Player.SetPosition',
             'objpath:/org/mpris/MediaPlayer2/Track/1', f'int64:{int(seconds * 1_000_000)}'],
            capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def command(self, *extra):
        return [sys.executable, '-m', 'lyric', '--player', PLAYER, *extra]

    def frames(self, seconds, *extra):
        from term_test import run
        return run(self.command(*extra), COLS, ROWS, seconds, env=self.env)

    def test_follows_position_seek_and_track_word(self):
        frames = self.frames(2, '--plain')
        self.assertTrue(any('HELLO' in '\n'.join(f['lines']) for f in frames))
        self.seek(6)
        frames = self.frames(2, '--plain')
        self.assertTrue(any('WORLD' in '\n'.join(f['lines']) for f in frames))

    def test_block_font_fills_the_screen(self):
        frames = self.frames(2)
        self.assertTrue(any('█' in '\n'.join(f['lines']) for f in frames))

    def test_footer_offset_and_clean_exit(self):
        import pyte
        master, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', ROWS, COLS, 0, 0))
            original = termios.tcgetattr(slave)
            process = subprocess.Popen(self.command('--plain', '--info'),
                                       stdin=slave, stdout=slave, stderr=slave, env=self.env)
            screen = pyte.Screen(COLS, ROWS)
            stream = pyte.ByteStream(screen)

            def wait_for(predicate, seconds=4):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    if select.select([master], [], [], .05)[0]:
                        stream.feed(os.read(master, 65536))
                    if predicate('\n'.join(screen.display)):
                        return True
                return False

            self.assertTrue(wait_for(lambda text: 'native word sync' in text))
            self.assertTrue(wait_for(lambda text: 'HELLO' in text))
            os.write(master, b'+')
            self.assertTrue(wait_for(lambda text: '+0.05s' in text))
            os.write(master, b'0')
            self.assertTrue(wait_for(lambda text: '+0.00s' in text))
            os.write(master, b'q')
            self.assertEqual(process.wait(timeout=5), 0)
            self.assertEqual(termios.tcgetattr(slave), original)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            os.close(master)
            os.close(slave)

    def test_list_players_sees_the_fake_player(self):
        result = subprocess.run([sys.executable, '-m', 'lyric', '--list-players'],
                                capture_output=True, text=True, env=self.env, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('fakeplayer', result.stdout)
        self.assertIn('Adapter Test', result.stdout)

    def test_fetch_uses_the_existing_cache(self):
        result = subprocess.run([sys.executable, '-m', 'lyric', '--fetch', '--player', PLAYER],
                                capture_output=True, text=True, env=self.env, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Adapter Test - Render Test.wlrc', result.stdout)


if __name__ == '__main__':
    unittest.main()
