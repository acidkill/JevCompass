"""Behavior tests for the profile cache and its service consumer."""
import unittest

from profile_cache import cached_profile
from profile_service import load_profile


def _provider(tenant_id, profile_id):
    return {"tenant": tenant_id, "profile": profile_id}


class ProfileCacheTests(unittest.TestCase):
    def test_same_tenant_reuses_case_insensitive_profile_key(self):
        cache = {}
        first = cached_profile(cache, "north", "USER-7", _provider)
        second = cached_profile(cache, "north", "user-7", _provider)
        self.assertIs(first, second)
        self.assertEqual(first["tenant"], "north")

    def test_identical_profile_ids_are_isolated_between_tenants(self):
        cache = {}
        north = cached_profile(cache, "north", "USER-7", _provider)
        south = cached_profile(cache, "south", "USER-7", _provider)
        self.assertEqual(north["tenant"], "north")
        self.assertEqual(south["tenant"], "south")

    def test_service_consumer_does_not_cross_tenant_cache_entries(self):
        cache = {}
        north = load_profile(cache, "north", "USER-7", _provider)
        south = load_profile(cache, "south", "USER-7", _provider)
        self.assertEqual((north["tenant"], south["tenant"]), ("north", "south"))


if __name__ == "__main__":
    unittest.main()
