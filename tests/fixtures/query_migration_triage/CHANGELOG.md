# Changelog

## 2025-07-01 — Query protocol v2

The upstream request format migrated from the v1 URI query convention to form-style query encoding. The v2 contract is authoritative for current requests. Preserve pair order and literal plus escaping; only space representation changes from `%20` to `+`.
