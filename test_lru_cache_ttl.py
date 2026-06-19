"""Tests for LRUCacheWithTTL and lru_cache_ttl decorator."""

import threading
import time
import unittest

from lru_cache_ttl import LRUCacheWithTTL, lru_cache_ttl


class TestLRUCacheWithTTL(unittest.TestCase):

    def test_basic_put_get(self):
        cache = LRUCacheWithTTL(maxsize=3, ttl=10)
        cache.put("a", 1)
        cache.put("b", 2)
        self.assertEqual(cache.get("a"), 1)
        self.assertEqual(cache.get("b"), 2)
        self.assertIsNone(cache.get("c"))

    def test_lru_eviction(self):
        cache = LRUCacheWithTTL(maxsize=2, ttl=10)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.get("a")          # "a" is now most recently used
        cache.put("c", 3)       # "b" should be evicted
        self.assertEqual(cache.get("a"), 1)
        self.assertIsNone(cache.get("b"), "b should have been evicted")
        self.assertEqual(cache.get("c"), 3)

    def test_ttl_expiry(self):
        cache = LRUCacheWithTTL(maxsize=10, ttl=0.1)
        cache.put("x", 42)
        self.assertEqual(cache.get("x"), 42)
        time.sleep(0.15)
        self.assertIsNone(cache.get("x"), "entry should have expired")

    def test_no_ttl(self):
        cache = LRUCacheWithTTL(maxsize=10, ttl=0)
        cache.put("k", "v")
        time.sleep(0.05)
        self.assertEqual(cache.get("k"), "v")

    def test_contains(self):
        cache = LRUCacheWithTTL(maxsize=5, ttl=10)
        cache.put("z", 99)
        self.assertIn("z", cache)
        self.assertNotIn("missing", cache)

    def test_delete(self):
        cache = LRUCacheWithTTL(maxsize=5, ttl=10)
        cache.put("d", "delete-me")
        self.assertTrue(cache.delete("d"))
        self.assertFalse(cache.delete("d"))
        self.assertIsNone(cache.get("d"))

    def test_clear(self):
        cache = LRUCacheWithTTL(maxsize=5, ttl=10)
        cache.put("a", 1)
        cache.put("b", 2)
        cache.clear()
        self.assertEqual(len(cache), 0)

    def test_evict_expired(self):
        cache = LRUCacheWithTTL(maxsize=10, ttl=0.05)
        cache.put("e1", 1)
        cache.put("e2", 2)
        time.sleep(0.1)
        removed = cache.evict_expired()
        self.assertEqual(removed, 2)
        self.assertEqual(len(cache), 0)

    def test_update_refreshes_expiry(self):
        cache = LRUCacheWithTTL(maxsize=5, ttl=0.15)
        cache.put("r", "old")
        time.sleep(0.1)
        cache.put("r", "new")   # refresh
        time.sleep(0.1)
        self.assertEqual(cache.get("r"), "new", "refreshed entry should still be alive")

    def test_thread_safety(self):
        cache = LRUCacheWithTTL(maxsize=100, ttl=5)
        errors = []

        def writer():
            try:
                for i in range(200):
                    cache.put(f"k{i % 50}", i)
            except Exception as exc:
                errors.append(exc)

        def reader():
            try:
                for i in range(200):
                    cache.get(f"k{i % 50}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer) for _ in range(4)]
        threads += [threading.Thread(target=reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [], f"Thread errors: {errors}")

    def test_invalid_maxsize(self):
        with self.assertRaises(ValueError):
            LRUCacheWithTTL(maxsize=0)


class TestDecorator(unittest.TestCase):

    def test_basic_caching(self):
        call_count = 0

        @lru_cache_ttl(maxsize=10, ttl=10)
        def add(a, b):
            nonlocal call_count
            call_count += 1
            return a + b

        self.assertEqual(add(1, 2), 3)
        self.assertEqual(add(1, 2), 3)
        self.assertEqual(call_count, 1, "second call should hit cache")

    def test_cache_info(self):
        @lru_cache_ttl(maxsize=5, ttl=10)
        def square(n):
            return n * n

        square(3)
        square(3)
        square(4)
        info = square.cache_info()
        self.assertEqual(info["hits"], 1)
        self.assertEqual(info["misses"], 2)

    def test_cache_clear(self):
        call_count = 0

        @lru_cache_ttl(maxsize=5, ttl=10)
        def fn(x):
            nonlocal call_count
            call_count += 1
            return x

        fn(1)
        fn.cache_clear()
        fn(1)
        self.assertEqual(call_count, 2)

    def test_ttl_expiry(self):
        call_count = 0

        @lru_cache_ttl(maxsize=5, ttl=0.1)
        def greet(name):
            nonlocal call_count
            call_count += 1
            return f"hello {name}"

        greet("world")
        time.sleep(0.15)
        greet("world")
        self.assertEqual(call_count, 2, "expired entry should trigger re-computation")

    def test_typed_cache(self):
        call_count = 0

        @lru_cache_ttl(maxsize=10, ttl=10, typed=True)
        def identity(x):
            nonlocal call_count
            call_count += 1
            return x

        identity(3)
        identity(3.0)
        self.assertEqual(call_count, 2, "typed=True: int and float should be separate")


if __name__ == "__main__":
    unittest.main(verbosity=2)
