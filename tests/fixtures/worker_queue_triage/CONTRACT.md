# Worker queue contract

`WorkerQueue(capacity=1)` is a bounded multi-producer, multi-consumer FIFO queue.

- `put(value, timeout=None)` enqueues one arbitrary Python value. If capacity is full, it waits for available capacity until timeout; expiration raises `TimeoutError` and does not enqueue or replace an item.
- `take(timeout=None)` removes and returns exactly the oldest queued value. Already queued values are available with `timeout=0`, including `None`, `False`, zero, and empty strings. An empty queue waits for publication until timeout; expiration raises `TimeoutError`.
- A successful enqueue wakes a waiting consumer; a successful removal wakes a producer waiting for capacity. Repeated/spurious wakeups must re-check the queue predicate while holding the condition lock. No lost wakeups are allowed.
- Preserve object identity, FIFO order, and exactly-once consumption.
- A timeout must not discard later work or leave an incorrect capacity count.
- Calls terminate within the supplied timeout plus normal scheduling overhead. Tests use real bounded synchronization and events, with no sleeps, network, or external dependencies.

The test-only wait events are synchronization aids for deterministic bounded tests. `trace_snapshot()` exposes only booleans for observed progress/contention and `None` for wait-condition satisfiability, which this initial trace does not observe. These fields do not alter queue behavior.
