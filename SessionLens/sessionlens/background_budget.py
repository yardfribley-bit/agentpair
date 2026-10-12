"""Cooperative wall-time budget for derived/history work, never live reads.

This is a scheduling duty target, not an operating-system CPU quota. A single
batch can exceed it; completed batches keep their durable checkpoints.
"""
import time


class BackgroundBudget:
    def __init__(self, fraction=.15, clock=None):
        if not 0 < fraction <= 1:
            raise ValueError('background fraction must be in (0, 1]')
        self.fraction = fraction
        self.clock = clock or time.monotonic
        self.next_at = 0.

    def ready(self):
        return self.clock() >= self.next_at

    def finish(self, started):
        now = self.clock()
        elapsed = max(0., now - started)
        self.next_at = now + elapsed * (1. / self.fraction - 1.)

    def delay(self, minimum=0.):
        return max(minimum, self.next_at - self.clock())
