#!/usr/bin/env python3
"""Minimal MPRIS media player for testing MPRIS-driven apps.

Usage:
  fake_mpris.py --file /path/track.mp3 --artist "Rick Astley" \
                --title "Never Gonna Give You Up" --duration 213.56 \
                --start 18.5 [--paused] [--rate 1.0]

Exposes org.mpris.MediaPlayer2.fakeplayer on the session bus and advances
Position in real time while PlaybackStatus is "Playing".
"""
import argparse
import time

import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

PLAYER = 'org.mpris.MediaPlayer2.fakeplayer'
PATH = '/org/mpris/MediaPlayer2'
IFACE_PLAYER = 'org.mpris.MediaPlayer2.Player'
IFACE_ROOT = 'org.mpris.MediaPlayer2'
IFACE_PROPS = 'org.freedesktop.DBus.Properties'

SPEC_VERSION = '2.2'


class Player(dbus.service.Object):
    def __init__(self, bus, args):
        super().__init__(bus, PATH)
        self.args = args
        self.duration = float(args.duration)
        self.start_wall = time.monotonic() - float(args.start)
        self.playing = not args.paused
        self.paused_at = float(args.start)
        self.rate = float(args.rate)

    # --- helpers -----------------------------------------------------------
    def position_us(self):
        if self.playing:
            pos = (time.monotonic() - self.start_wall) * self.rate
        else:
            pos = self.paused_at
        pos = min(max(pos, 0.0), self.duration)
        return int(pos * 1_000_000)

    def metadata(self):
        return dbus.Dictionary({
            'mpris:trackid': dbus.ObjectPath('/org/mpris/MediaPlayer2/Track/1'),
            'mpris:length': dbus.Int64(int(self.duration * 1_000_000)),
            'xesam:title': dbus.String(self.args.title),
            'xesam:artist': dbus.Array([dbus.String(self.args.artist)], signature='s'),
            'xesam:album': dbus.String(self.args.album),
            'xesam:url': dbus.String('file://' + self.args.file),
        }, signature='sv')

    # --- Properties --------------------------------------------------------
    def _get(self, iface, prop):
        if iface != IFACE_PLAYER:
            if prop == 'Identity':
                return dbus.String('Fake MPRIS Player')
            if prop == 'CanQuit':
                return dbus.Boolean(True)
            if prop == 'DesktopEntry':
                return dbus.String('fakeplayer')
            if prop == 'SupportedUriSchemes':
                return dbus.Array(['file'], signature='s')
            if prop == 'SupportedMimeTypes':
                return dbus.Array(['audio/mpeg'], signature='s')
            raise dbus.exceptions.DBusException(
                f'No such property {prop}', name='org.freedesktop.DBus.Error.InvalidArgs')
        if prop == 'PlaybackStatus':
            return dbus.String('Playing' if self.playing else 'Paused')
        if prop == 'Position':
            return dbus.Int64(self.position_us())
        if prop == 'Metadata':
            return self.metadata()
        if prop == 'Rate':
            return dbus.Double(self.rate)
        if prop in ('CanPlay', 'CanPause', 'CanSeek', 'CanGoNext', 'CanGoPrevious',
                    'CanControl'):
            return dbus.Boolean(True)
        if prop == 'Volume':
            return dbus.Double(1.0)
        if prop == 'MinimumRate':
            return dbus.Double(0.1)
        if prop == 'MaximumRate':
            return dbus.Double(4.0)
        raise dbus.exceptions.DBusException(
            f'No such property {prop}', name='org.freedesktop.DBus.Error.InvalidArgs')

    @dbus.service.method(IFACE_PROPS, in_signature='ss', out_signature='v')
    def Get(self, iface, prop):
        return self._get(iface, prop)

    @dbus.service.method(IFACE_PROPS, in_signature='s', out_signature='a{sv}')
    def GetAll(self, iface):
        props = {}
        for name in ('PlaybackStatus', 'Position', 'Metadata', 'Rate', 'Volume',
                     'CanPlay', 'CanPause', 'CanSeek', 'CanControl'):
            try:
                props[name] = self._get(iface, name)
            except dbus.exceptions.DBusException:
                pass
        return dbus.Dictionary(props, signature='sv')

    @dbus.service.method(IFACE_PROPS, in_signature='ssv')
    def Set(self, iface, prop, value):
        if iface == IFACE_PLAYER and prop == 'Rate':
            self.rate = float(value)

    @dbus.service.signal(IFACE_PROPS, signature='sa{sv}as')
    def PropertiesChanged(self, iface, changed, invalidated):
        pass

    def _emit(self):
        self.PropertiesChanged(
            IFACE_PLAYER,
            {'PlaybackStatus': dbus.String('Playing' if self.playing else 'Paused')},
            dbus.Array([], signature='s'))

    # --- Player methods ----------------------------------------------------
    @dbus.service.method(IFACE_PLAYER)
    def Play(self):
        if not self.playing:
            self.start_wall = time.monotonic() - self.paused_at / self.rate
            self.playing = True
            self._emit()

    @dbus.service.method(IFACE_PLAYER)
    def Pause(self):
        if self.playing:
            self.paused_at = self.position_us() / 1_000_000
            self.playing = False
            self._emit()

    @dbus.service.method(IFACE_PLAYER)
    def PlayPause(self):
        self.Pause() if self.playing else self.Play()

    @dbus.service.method(IFACE_PLAYER, in_signature='x')
    def Seek(self, offset):
        target = self.position_us() / 1_000_000 + offset / 1_000_000
        self._set_pos(target)

    @dbus.service.method(IFACE_PLAYER, in_signature='ox')
    def SetPosition(self, trackid, position):
        self._set_pos(position / 1_000_000)

    @dbus.service.method(IFACE_PLAYER)
    def Stop(self):
        self.paused_at = 0.0
        self.playing = False
        self._emit()

    def _set_pos(self, seconds):
        seconds = min(max(seconds, 0.0), self.duration)
        if self.playing:
            self.start_wall = time.monotonic() - seconds / self.rate
        else:
            self.paused_at = seconds
        self.PropertiesChanged(IFACE_PLAYER, {}, dbus.Array(['Position'], signature='s'))

    # --- Root methods ------------------------------------------------------
    @dbus.service.method(IFACE_ROOT)
    def Raise(self):
        pass

    @dbus.service.method(IFACE_ROOT)
    def Quit(self):
        loop.quit()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--file', required=True)
    p.add_argument('--artist', default='Unknown Artist')
    p.add_argument('--title', required=True)
    p.add_argument('--album', default='')
    p.add_argument('--duration', type=float, required=True)
    p.add_argument('--start', type=float, default=0.0)
    p.add_argument('--rate', type=float, default=1.0)
    p.add_argument('--paused', action='store_true')
    args = p.parse_args()

    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    global loop
    loop = GLib.MainLoop()
    global busname
    busname = dbus.service.BusName(PLAYER, bus=bus, do_not_queue=True)
    Player(bus, args)
    print(f'fake MPRIS player up: {args.artist} - {args.title} '
          f'(start {args.start:.2f}s, {args.duration:.2f}s)', flush=True)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
