# Retry client contract

This fictional client calls `send()` at most `max_attempts` times; the initial call counts as attempt one. `max_attempts` must be at least one.

Retry only `ConnectionError`, HTTP 429, and HTTP 5xx. Return any other HTTP response without retrying. Propagate all other exceptions immediately. When a retryable response supplies a numeric `Retry-After` header, use that many seconds for the next delay; otherwise use `base_delay`. Never call `sleep()` after the final attempt. Return the last response when HTTP attempts are exhausted, and raise the last `ConnectionError` when connection attempts are exhausted.

All tests are local and use a fake `send` and `sleep`; no network service is involved.
