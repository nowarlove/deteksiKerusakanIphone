"""Rate limiter database dengan fallback per-instance untuk local/serverless."""

import os
import time
from collections import defaultdict, deque
from threading import Lock

from .supabase_store import hash_identifier


class RateLimiter:
    def __init__(self, store):
        self.store = store
        self.limit = int(os.environ.get("RATE_LIMIT_REQUESTS", "20"))
        self.window = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))
        self._events = defaultdict(deque)
        self._lock = Lock()

    def allow(self, client_identifier):
        identifier = hash_identifier(client_identifier or "unknown")
        distributed = self.store.check_rate_limit(identifier, self.limit, self.window)
        if distributed is not None:
            return distributed, getattr(self.store, "backend_name", "database")

        now = time.monotonic()
        with self._lock:
            events = self._events[identifier]
            cutoff = now - self.window
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False, "instance"
            events.append(now)
            return True, "instance"
