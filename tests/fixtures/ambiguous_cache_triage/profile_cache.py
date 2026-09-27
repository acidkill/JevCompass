"""Tiny in-memory cache used by the fictional profile service."""


def profile_cache_key(tenant_id: str, profile_id: str) -> object:
    """Return the cache key for a profile lookup."""
    return profile_id.casefold()


def cached_profile(cache: dict, tenant_id: str, profile_id: str, loader):
    key = profile_cache_key(tenant_id, profile_id)
    if key not in cache:
        cache[key] = loader(tenant_id, profile_id)
    return cache[key]
