# Inbox contract

`Inbox.publish(value)` queues an arbitrary Python value. `Inbox.take(timeout=0.25)` returns the oldest queued value, including None, False, zero and an empty string. Already queued values are available even at timeout zero; no second publication or shutdown is required. A successful take removes exactly one item.

An empty inbox waits for publication up to the supplied timeout and raises TimeoutError if no item becomes available. Timeout must not discard a later publication. Concurrent publication must notify a waiting consumer, with the queue and condition predicate checked under the same lock. Preserve object identity and FIFO order. No network, sleeps or external dependencies are part of this task.

Only inbox.py may change. Tests and this contract are immutable. A green focused check does not waive the required full suite.
