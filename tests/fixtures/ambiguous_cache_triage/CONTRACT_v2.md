# Profile cache contract v2

Effective 2026-04-01 and superseding v1. Profile identifiers are case-insensitive within a tenant. Cache identity is the pair of tenant identifier and case-folded profile identifier. A cached response for one tenant must never be returned for another tenant.
