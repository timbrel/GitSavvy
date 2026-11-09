from unittest import TestCase

from GitSavvy.core.caches import Cache


class TestCache(TestCase):
    def test_update_from_cache_preserves_source_order(self):
        source = Cache()
        source.update(a=1, b=2, c=3)
        source["a"]
        target = Cache()

        target.update(source)

        self.assertEqual([("b", 2), ("c", 3), ("a", 1)], list(target.items()))
        self.assertEqual(list(target.items()), list(source.items()))

    def test_update_from_self(self):
        cache = Cache()
        cache.update(a=1, b=2)

        cache.update(cache)

        self.assertEqual([("a", 1), ("b", 2)], list(cache.items()))

    def test_update_from_cache_respects_capacity_and_keyword_overrides(self):
        source = Cache()
        source.update(a=1, b=2, c=3)
        target = Cache(maxsize=2)

        target.update(source, b=4)

        self.assertEqual([("c", 3), ("b", 4)], list(target.items()))

    def test_copy_preserves_capacity_and_access_order(self):
        source = Cache(maxsize=2)
        source.update(a=1, b=2)
        source["a"]

        copied = source.copy()

        self.assertIsInstance(copied, Cache)
        self.assertIsNot(copied, source)
        self.assertEqual(2, copied.maxsize)
        self.assertEqual([("b", 2), ("a", 1)], list(copied.items()))
        self.assertEqual(list(copied.items()), list(source.items()))

        copied["c"] = 3
        self.assertEqual([("a", 1), ("c", 3)], list(copied.items()))
        self.assertEqual([("b", 2), ("a", 1)], list(source.items()))

    def test_update_accepts_mapping_pairs_and_keywords(self):
        cache = Cache()
        cache.update({"a": 1})
        cache.update([("b", 2)], c=3)

        self.assertEqual([("a", 1), ("b", 2), ("c", 3)], list(cache.items()))
