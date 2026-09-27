"""Consumer path for retrieving tenant-scoped profiles."""
from profile_cache import cached_profile


def load_profile(cache, tenant_id, profile_id, provider):
    return cached_profile(cache, tenant_id, profile_id, provider)
