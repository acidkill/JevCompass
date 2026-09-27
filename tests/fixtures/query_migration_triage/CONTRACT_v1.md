# Query serialization contract, v1

Effective through 2025-06-30. Query pairs preserve caller order. For each value, encode UTF-8 bytes and percent-escape spaces as `%20`; a literal plus is `%2B`. This was the legacy RFC 3986-style representation.
