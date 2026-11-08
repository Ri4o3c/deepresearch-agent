"""Ephemeral, bounded search cache. No credentials, queries or URLs are logged."""
import copy
import logging
import os
import time
from collections import OrderedDict
from contextvars import ContextVar
from threading import RLock
from urllib.parse import urldefrag

current_session = ContextVar("search_session", default=None)


def url_key(source):
    url = source["url"]
    # Local document fragments encode page identity and must survive deduplication.
    return url if source.get("type") == "local" or url.startswith("/api/kb/") else urldefrag(url)[0]


def unique_sources(rows):
    seen, result = set(), []
    for row in rows:
        if not row.get("url"):
            continue
        key = url_key(row)
        if key not in seen:
            seen.add(key)
            result.append(dict(row))
    return result


class SearchSession:
    def __init__(self, capacity=None, ttl=None, clock=time.monotonic):
        self.capacity = max(0, int(os.environ.get("SEARCH_CACHE_SIZE", "32")) if capacity is None else capacity)
        self.ttl = max(0, float(os.environ.get("SEARCH_CACHE_TTL", "120")) if ttl is None else ttl)
        self.clock, self.entries, self.lock = clock, OrderedDict(), RLock()
        self.metrics = dict(search_calls=0, fetch_calls=0, cache_hits=0, cache_misses=0,
                            cache_bypass=0, context_chars=0)

    def search(self, key, run, bypass=False):
        # Per-session lock coalesces simultaneous misses; independent research calls never share a lock/cache.
        with self.lock:
            now = self.clock()
            for expired in [k for k, (deadline, _) in self.entries.items() if deadline <= now]:
                del self.entries[expired]
            if bypass:
                self.metrics["cache_bypass"] += 1
                # Invalidate old data even if the fresh attempt fails.
                self.entries.pop(key, None)
                return run()
            if key in self.entries:
                self.metrics["cache_hits"] += 1
                self.entries.move_to_end(key)
                return copy.deepcopy(self.entries[key][1])
            self.metrics["cache_misses"] += 1
            rows = run()  # exceptions and empty results are never cached
            if rows and self.capacity and self.ttl:
                self.entries[key] = (self.clock() + self.ttl, copy.deepcopy(rows))
                while len(self.entries) > self.capacity:
                    self.entries.popitem(last=False)
            return rows

    def __enter__(self):
        self.started = time.monotonic()
        self.token = current_session.set(self)
        return self

    def __exit__(self, *exc):
        current_session.reset(self.token)
        logging.getLogger("search").info("research_metrics %s elapsed_s=%.3f", self.metrics,
                                         time.monotonic() - self.started)
        self.entries.clear()
