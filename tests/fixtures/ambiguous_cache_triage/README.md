# Repair the profile cache

A focused test fails when two tenants request the same profile identifier. Determine whether the implementation or the expected behavior is stale by inspecting the current contract, its history, the golden example, the consumer, and the tests.

Make a source-only repair in `profile_cache.py`. Keep the function signatures and same-tenant cache behavior. Do not alter the tests, contracts, changelog, golden example, or `profile_service.py`. Do not invent requirements. Run the configured focused tests, then the full fixture tests, and check the diff. Record the initial failure separately from post-edit checks.
