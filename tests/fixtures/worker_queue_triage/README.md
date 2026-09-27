# Bounded worker queue timeout case

Implement the bounded FIFO queue in `worker_queue.py` without changing its public methods. The frozen contract and tests are the same for baseline and treatment.

The same initial bounded trace is available to both arms: `progress_observed=true`, `resource_contention_observed=true`, and `wait_condition_satisfiable=null`. It is exposed in the tests through the fixture's trace snapshot, without raw runtime output. These observations alone do not identify the defect.

This fixture's starter defect is locally resolvable by inspecting the source: the producer and consumer wait on distinct condition variables that share a lock, and each operation notifies the wrong condition. A missing wakeup follows directly from that mismatch. Treat this as a local-resolution/no-call control, not as a remote-usefulness eligible case. Do not add obscuring layers or artificial delays to make it appear ambiguous.

Only change `worker_queue.py`. Run focused `python -m unittest discover -s tests -p test_worker_queue.py -v`, then required full `python -m unittest discover -s tests -v` as separate commands. Both must complete within bounded time. Keep contract and tests unchanged. Do not reinterpret a timeout as success.

The fixture is stdlib-only and makes no network calls. It is synthetic code, not an operating-system sandbox. The outer fixture verifier uses disposable copies and separately checks the starter, a correct reference repair, and plausible mutants.
