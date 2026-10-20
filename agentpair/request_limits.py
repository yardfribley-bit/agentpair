"""Bounded, thread-safe per-client and per-account request windows."""
from collections import OrderedDict, deque
import hashlib
import threading
import time


class RequestLimits:
    def __init__(self, capacity=10000):
        self.capacity = capacity
        self.windows = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, category, client, account='', limit=10, seconds=60):
        now = time.monotonic()
        keys = [(category, 'ip', client)]
        if account:
            digest = hashlib.sha256(str(account).casefold().encode()).hexdigest()
            keys.append((category, 'account', digest))
        with self.lock:
            for key in keys:
                window = self.windows.setdefault(key, deque())
                self.windows.move_to_end(key)
                while window and now - window[0] >= seconds:
                    window.popleft()
            if any(len(self.windows[key]) >= limit for key in keys):
                return False
            for key in keys:
                self.windows[key].append(now)
            while len(self.windows) > self.capacity:
                self.windows.popitem(last=False)
            return True
