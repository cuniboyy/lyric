"""MPRIS player discovery, ranking and transport control over D-Bus.

Everything talks to the bus directly: no ``playerctl`` process is spawned, so
sampling the player never blocks and no external dependency is required.
"""
import asyncio
import logging

from dbus_next import Message, MessageType

OBJECT_PATH = '/org/mpris/MediaPlayer2'
PLAYER_INTERFACE = 'org.mpris.MediaPlayer2.Player'
ROOT_INTERFACE = 'org.mpris.MediaPlayer2'
PROPERTIES = 'org.freedesktop.DBus.Properties'
DBUS = 'org.freedesktop.DBus'
DBUS_PATH = '/org/freedesktop/DBus'

PREFIX = 'org.mpris.MediaPlayer2.'
PLAYERCTLD = PREFIX + 'playerctld'
PLAYERCTLD_INTERFACE = 'com.github.altdesktop.playerctld'

# Higher wins. Anything unrecognised (unresponsive, wedged) ranks below Stopped.
RANK = {'Playing': 3, 'Paused': 2, 'Stopped': 1}


def rank(status):
    return RANK.get(status, 0)


def plain(value):
    """dbus-next wraps variant bodies in a ``.value`` attribute."""
    return value.value if hasattr(value, 'value') else value


def unwrap_dictionary(value):
    return {key: plain(item) for key, item in plain(value).items()}


async def call(bus, destination, member, interface=PLAYER_INTERFACE, path=OBJECT_PATH,
               signature='', body=None, timeout=.6):
    """One D-Bus method call; None on timeout, transport error or a DBus error reply."""
    message = Message(destination=destination, path=path, interface=interface,
                      member=member, signature=signature, body=body or [])
    try:
        reply = await asyncio.wait_for(bus.call(message), timeout=timeout)
    except (asyncio.TimeoutError, OSError):
        return None
    return None if reply.message_type == MessageType.ERROR else reply


async def property_of(bus, service, interface, name, timeout=.6):
    reply = await call(bus, service, 'Get', PROPERTIES, OBJECT_PATH, 'ss',
                       [interface, name], timeout)
    if reply is None or not reply.body:
        return None
    return plain(reply.body[0])


async def list_names(bus):
    reply = await call(bus, DBUS, 'ListNames', DBUS, DBUS_PATH, timeout=1)
    if reply is None or not reply.body:
        return []
    return [str(name) for name in plain(reply.body[0])]


async def has_owner(bus, name):
    reply = await call(bus, DBUS, 'NameHasOwner', DBUS, DBUS_PATH, 's', [name])
    return bool(reply and reply.body and reply.body[0])


async def player_names(bus):
    """Active players, most recently used first.

    ``playerctld`` orders players by recency, which is a better default than
    the bus name order. It is only consulted when it already owns a name, so
    asking never auto-activates the daemon.
    """
    if await has_owner(bus, PLAYERCTLD):
        names = await property_of(bus, PLAYERCTLD, PLAYERCTLD_INTERFACE, 'PlayerNames')
        ordered = [str(name) for name in names] if names else []
        if ordered:
            return ordered
    return [name for name in await list_names(bus)
            if name.startswith(PREFIX) and name != PLAYERCTLD]


async def status(bus, service):
    value = await property_of(bus, service, PLAYER_INTERFACE, 'PlaybackStatus')
    return str(value) if value is not None else None


async def identity(bus, service):
    value = await property_of(bus, service, ROOT_INTERFACE, 'Identity')
    return str(value) if value is not None else None


async def player_state(bus):
    """Map service name -> PlaybackStatus for every player currently on the bus.

    Probes run concurrently and each is individually timeout-bounded, so one
    wedged player cannot hold up discovery of the others.
    """
    names = await player_names(bus)
    if not names:
        return {}
    states = await asyncio.gather(*(status(bus, name) for name in names))
    return {name: value for name, value in zip(names, states) if value is not None}


def choose(current, states):
    """Pick the player to follow from ``{service: PlaybackStatus}``.

    The current player is kept on a tie so the display never bounces between
    two equally active players; a strictly more active player takes over
    immediately, which is what makes handing off from a paused player to a
    freshly started one feel instant.
    """
    if not states:
        return None
    best, best_rank = None, -1
    for name, value in states.items():
        current_rank = rank(value)
        if current_rank > best_rank:
            best, best_rank = name, current_rank
    if current in states and rank(states[current]) >= best_rank:
        return current
    return best


async def transport(bus, service, member):
    """Send a no-argument transport method (PlayPause, Next, Previous)."""
    if not service:
        return False
    return await call(bus, service, member) is not None


def send_transport(service, member):
    """Fire-and-forget transport call for use from a plain worker thread."""
    if not service:
        return False

    async def run():
        from dbus_next.aio import MessageBus
        bus = await MessageBus().connect()
        try:
            return await transport(bus, service, member)
        finally:
            bus.disconnect()

    try:
        return asyncio.run(run())
    except Exception:
        logging.exception('Transport call %s to %s failed', member, service)
        return False


async def any_players():
    """True when at least one MPRIS player is on the bus."""
    from dbus_next.aio import MessageBus
    bus = await MessageBus().connect()
    try:
        return bool(await player_state(bus))
    finally:
        bus.disconnect()


async def describe():
    """Identity, status, metadata and position for every player, for --list-players."""
    from dbus_next.aio import MessageBus
    bus = await MessageBus().connect()
    try:
        states = await player_state(bus)
        report = []
        for name, state in states.items():
            meta = await metadata(bus, name)
            report.append({
                'service': name,
                'player': name[len(PREFIX):],
                'identity': await identity(bus, name) or '',
                'status': state,
                'artist': meta.get('artist', '') if meta else '',
                'title': meta.get('title', '') if meta else '',
            })
        return report
    finally:
        bus.disconnect()


def decode_metadata(value):
    """Turn a raw ``Metadata`` variant into a small dict, or None when unplayable."""
    if value is None:
        return None
    data = unwrap_dictionary(value)
    title = str(data.get('xesam:title', ''))
    if not title:
        return None
    artists = data.get('xesam:artist', [])
    artist = ', '.join(plain(item) for item in artists) if isinstance(artists, list) else str(artists)
    try:
        duration = float(data.get('mpris:length', 0)) / 1_000_000
    except (TypeError, ValueError):
        duration = 0.0
    return {'artist': artist, 'title': title, 'duration': duration,
            'album': str(data.get('xesam:album', ''))}


async def metadata(bus, service):
    """Decode (artist, title, duration, album) from a player's Metadata property."""
    return decode_metadata(await property_of(bus, service, PLAYER_INTERFACE, 'Metadata'))


async def current(info_source=None):
    """One-shot 'what is playing', used by --fetch, --export and --list-players."""
    from dbus_next.aio import MessageBus
    bus = await MessageBus().connect()
    try:
        states = await player_state(bus)
        name = info_source if info_source in states else choose(None, states)
        if not name:
            return None
        track = await metadata(bus, name)
        if not track:
            return None
        return (track['artist'], track['title'], track['duration'], track['album'])
    finally:
        bus.disconnect()


def log_discovery(states, target):
    logging.debug('MPRIS discovery: %s -> %s', states, target)
