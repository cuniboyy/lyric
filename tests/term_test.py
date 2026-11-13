#!/usr/bin/env python3
"""Run a command in a pty, decode its output with pyte, and sample the screen.

Usage: term_test.py COLS ROWS SECONDS OUT.json -- command args...
"""
import fcntl
import json
import os
import pty
import select
import struct
import sys
import termios
import time

import pyte


def run(cmd, cols, rows, seconds, env=None):
    pid, fd = pty.fork()
    if pid == 0:
        os.environ['TERM'] = 'xterm-256color'
        if env:
            os.environ.update(env)
        os.execvp(cmd[0], cmd)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

    screen = pyte.Screen(cols, rows)
    stream = pyte.ByteStream(screen)

    samples = []
    deadline = time.time() + seconds
    last_dump = 0.0
    t0 = time.time()
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.05)
        if fd in r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            stream.feed(data)
        now = time.time()
        if now - last_dump >= 0.25:
            last_dump = now
            samples.append({
                't': round(now - t0, 2),
                'lines': [line.rstrip() for line in screen.display],
            })
    os.kill(pid, 15)
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass
    os.close(fd)
    return samples


if __name__ == '__main__':
    cols, rows, seconds, out = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
    cmd = sys.argv[sys.argv.index('--') + 1:]
    samples = run(cmd, cols, rows, seconds)
    with open(out, 'w') as f:
        json.dump(samples, f, indent=1)
    print(f'{len(samples)} samples -> {out}')
