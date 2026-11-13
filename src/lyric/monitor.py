"""Background MPRIS sampling with automatic player discovery and handoff."""
import asyncio
from dataclasses import dataclass
import logging
import threading
import time

from dbus_next import MessageType
from dbus_next.aio import MessageBus

from lyric import players

# Fast enough that the interpolated clock never needs a long extrapolation.
REFRESH = .08
# Safety net for players that change state without announcing it.
REDISCOVER = 2.0
STALE_AFTER = 2.0


@dataclass(frozen=True)
class Sample:
    info: tuple
    position: float
    status: str
    when: float
    epoch: int
    player: str


class Monitor:
    """One persistent D-Bus connection; rendering never waits on IPC.

    Runs its own asyncio loop in a daemon thread and publishes immutable
    ``Sample`` snapshots. It follows whatever is actually playing: a newly
    started player takes over immediately, while a tie keeps the current one
    so the display does not bounce.
    """

    def __init__(self, stop, player=None):
        self.stop = stop
        self.pinned = player
        self.target = player
        self.sample = None
        self.epoch = 0
        self.dirty = True
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def run(self):
        while not self.stop.is_set():
            try:
                asyncio.run(self.poll())
            except Exception:
                self.sample = None
                logging.exception('MPRIS monitor failed; reconnecting')
                self.stop.wait(1)

    async def add_matches(self, bus):
        rules = [
            f"type='signal',interface='{players.PLAYER_INTERFACE}',"
            f"member='Seeked',path='{players.OBJECT_PATH}'",
            f"type='signal',interface='{players.PROPERTIES}',"
            f"member='PropertiesChanged',path='{players.OBJECT_PATH}'",
            f"type='signal',interface='{players.DBUS}',member='NameOwnerChanged'",
        ]
        for rule in rules:
            await players.call(bus, players.DBUS, 'AddMatch', players.DBUS,
                               players.DBUS_PATH, 's', [rule])

    def on_message(self, message):
        if message.message_type != MessageType.SIGNAL:
            return
        if message.member == 'Seeked':
            self.epoch += 1
        elif message.member == 'PropertiesChanged':
            changed = message.body[1] if len(message.body) > 1 else {}
            if 'PlaybackStatus' in changed:
                self.dirty = True
        elif message.member == 'NameOwnerChanged':
            name = str(message.body[0]) if message.body else ''
            if name.startswith(players.PREFIX):
                self.dirty = True

    async def poll(self):
        bus = await MessageBus().connect()
        bus.add_message_handler(self.on_message)
        await self.add_matches(bus)
        last_discovery = -REDISCOVER
        try:
            while not self.stop.is_set():
                started = time.monotonic()
                if self.dirty or not self.target or started - last_discovery >= REDISCOVER:
                    await self.discover(bus)
                    last_discovery = time.monotonic()
                    self.dirty = False
                if self.target:
                    await self.read(bus, started)
                await asyncio.sleep(REFRESH)
        finally:
            bus.disconnect()

    async def discover(self, bus):
        states = await players.player_state(bus)
        if self.pinned:
            target = self.pinned if self.pinned in states else None
        else:
            target = players.choose(self.target, states)
        if target != self.target:
            logging.info('Following %s', target)
            self.target = target
            self.sample = None
            # A different player is a different timeline; reset the clock.
            self.epoch += 1
        elif target is None:
            self.sample = None
        players.log_discovery(states, target)

    async def read(self, bus, started):
        reply = await players.call(bus, self.target, 'GetAll', players.PROPERTIES,
                                   players.OBJECT_PATH, 's', [players.PLAYER_INTERFACE])
        if reply is None or not reply.body:
            if self.sample and time.monotonic() - self.sample.when > STALE_AFTER:
                self.sample = None
            return
        values = players.unwrap_dictionary(reply.body[0])
        track = players.decode_metadata(values.get('Metadata'))
        if not track:
            self.sample = None
            return
        try:
            position = float(values.get('Position', 0)) / 1_000_000
        except (TypeError, ValueError):
            return
        self.sample = Sample(
            (track['artist'], track['title'], track['duration'], track['album']),
            position,
            str(values.get('PlaybackStatus', 'Stopped')),
            (started + time.monotonic()) / 2,
            self.epoch,
            self.target,
        )
