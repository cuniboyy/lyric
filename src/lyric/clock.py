"""Interpolate coarse MPRIS position samples into a smooth display clock."""


class PlaybackClock:
    """Follow a player's position without resetting on microsecond noise.

    Players report position at wildly different rates, so every sample is
    compared against the interpolated prediction. Small disagreements are
    slewed (75-125% speed) instead of snapping, which keeps the karaoke sweep
    from flickering; real seeks and pauses snap immediately.
    """

    def __init__(self):
        self.sample = None

    def position(self, track_id, raw, status, now):
        if self.sample is None or track_id != self.track_id or status != self.status:
            self.anchor = raw
            self.correction = 0
            self.track_id, self.sample, self.status, self.when = track_id, raw, status, now
        elif abs(raw - self.sample) > .03:
            predicted = self.current(now)
            error = raw - predicted
            # Ordinary clock corrections must never move the display backward.
            # Slew at 75-125% speed instead; actual seeks still snap immediately.
            if abs(error) > 2.5 or raw < self.sample - .5:
                self.anchor, self.correction = raw, 0
            else:
                self.anchor = predicted
                self.correction = max(-.25, min(.25, error))
            self.sample, self.when = raw, now
        return self.current(now)

    def current(self, now):
        if self.sample is None:
            return 0
        elapsed = min(2, max(0, now - self.when)) if self.status == 'Playing' else 0
        return self.anchor + elapsed + self.correction * min(elapsed, 1)
