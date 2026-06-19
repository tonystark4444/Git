"""
Thread-safe LRU cache with TTL expiration using only standard library modules.
"""

import threading
import time
from collections import OrderedDict
from functools import wraps
from typing import Any, Callable, Optional, Tuple


class LRUCacheWithTTL:
    """
    A thread-safe Least Recently Used (LRU) cache with per-entry TTL expiration.

    Entries are evicted when:
      - The cache exceeds `maxsize` (least recently used entry is dropped), or
      - An entry's TTL has elapsed (it is treated as a cache miss on next access).

    All public methods are safe to call from multiple threads concurrently.
    """

    def __init__(self, maxsize: int = 128, ttl: float = 300.0) -> None:
        """
        Args:
            maxsize: Maximum number of entries to hold. Must be >= 1.
            ttl:     Seconds before an entry expires. Use 0 or None to disable TTL.
        """
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        self._maxsize = maxsize
        self._ttl = ttl
        # OrderedDict preserves insertion order; we move accessed keys to the end
        # so the front always holds the least recently used entry.
        self._cache: OrderedDict[Any, Tuple[Any, Optional[float]]] = OrderedDict()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Core cache operations
    # ------------------------------------------------------------------

    def get(self, key: Any, default: Any = None) -> Any:
        """Return the cached value for *key*, or *default* on a miss/expiry."""
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return default

            value, expiry = entry
            if self._is_expired(expiry):
                del self._cache[key]
                return default

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            return value

    def put(self, key: Any, value: Any) -> None:
        """Insert or update *key* with *value*, respecting maxsize and TTL."""
        expiry = time.monotonic() + self._ttl if self._ttl else None
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = (value, expiry)
            else:
                self._cache[key] = (value, expiry)
                if len(self._cache) > self._maxsize:
                    # Evict the least recently used entry (front of OrderedDict)
                    self._cache.popitem(last=False)

    def delete(self, key: Any) -> bool:
        """Remove *key* from the cache. Returns True if the key existed."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        """Remove all entries from the cache."""
        with self._lock:
            self._cache.clear()

    def evict_expired(self) -> int:
        """
        Proactively remove all expired entries.

        This is optional — expired entries are also evicted lazily on access.
        Returns the number of entries removed.
        """
        now = time.monotonic()
        with self._lock:
            expired_keys = [
                k for k, (_, expiry) in self._cache.items()
                if expiry is not None and now >= expiry
            ]
            for k in expired_keys:
                del self._cache[k]
            return len(expired_keys)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def __contains__(self, key: Any) -> bool:
        """Return True if *key* is present and not expired."""
        return self.get(key, _SENTINEL) is not _SENTINEL

    def __len__(self) -> int:
        """Return the number of entries currently stored (including expired ones)."""
        with self._lock:
            return len(self._cache)

    def __repr__(self) -> str:
        with self._lock:
            return (
                f"{type(self).__name__}(maxsize={self._maxsize}, "
                f"ttl={self._ttl}, size={len(self._cache)})"
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_expired(expiry: Optional[float]) -> bool:
        return expiry is not None and time.monotonic() >= expiry


_SENTINEL = object()


# ---------------------------------------------------------------------------
# Decorator API
# ---------------------------------------------------------------------------

def lru_cache_ttl(
    maxsize: int = 128,
    ttl: float = 300.0,
    typed: bool = False,
) -> Callable:
    """
    Decorator that wraps a function with a thread-safe LRU cache with TTL.

    Args:
        maxsize: Maximum number of distinct call signatures to cache.
        ttl:     Seconds before a cached result expires.
        typed:   If True, arguments of different types are cached separately
                 (e.g. ``f(3)`` and ``f(3.0)`` are distinct cache entries).

    The wrapped function gains three helper attributes:
        - ``cache_info()``  → dict with hits, misses, maxsize, currsize
        - ``cache_clear()`` → evicts all entries
        - ``cache``         → the underlying LRUCacheWithTTL instance

    Example::

        @lru_cache_ttl(maxsize=256, ttl=60)
        def fetch_user(user_id: int) -> dict:
            ...
    """
    def decorator(func: Callable) -> Callable:
        cache = LRUCacheWithTTL(maxsize=maxsize, ttl=ttl)
        lock = threading.Lock()
        hits = 0
        misses = 0

        def _make_key(args: tuple, kwargs: dict) -> tuple:
            key = args
            if kwargs:
                key += (object(),) + tuple(sorted(kwargs.items()))
            if typed:
                key += tuple(type(a) for a in args)
                if kwargs:
                    key += tuple(type(v) for v in kwargs.values())
            return key

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            nonlocal hits, misses
            key = _make_key(args, kwargs)
            result = cache.get(key, _SENTINEL)
            if result is not _SENTINEL:
                with lock:
                    hits += 1
                return result
            with lock:
                misses += 1
            result = func(*args, **kwargs)
            cache.put(key, result)
            return result

        def cache_info() -> dict:
            with lock:
                return {
                    "hits": hits,
                    "misses": misses,
                    "maxsize": maxsize,
                    "currsize": len(cache),
                }

        wrapper.cache_info = cache_info          # type: ignore[attr-defined]
        wrapper.cache_clear = cache.clear        # type: ignore[attr-defined]
        wrapper.cache = cache                    # type: ignore[attr-defined]
        return wrapper

    return decorator
