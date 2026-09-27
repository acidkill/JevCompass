# Ledger Archive adapter contract (frozen)

## Scope

The app has a stable public function `ledger_adapter.load_document`. Only this
adapter is being migrated. The installed fictional dependency has changed from
its old positional lookup API to the v2 API in `ledger_archive.py`.

## Public app API to preserve

```python
load_document(client, document_key, stale_ok=False, cache=None)
```

The first two arguments and both optional arguments remain positional-or-keyword
for existing callers. Return `None` when the dependency entry's payload is
`None`; return an ordinary shallow `dict` copy when its payload is a mapping.
An empty mapping is a valid document and returns `{}`. A non-mapping,
non-`None` payload raises `TypeError`.

## Dependency v2 mapping

Call the client's keyword-only method as:

```python
client.open_document(
    document_key=document_key,
    allow_stale=stale_ok,
    cache_hint=cache_copy,
)
```

Pass `None` as `cache_hint` when `cache` is `None`; otherwise pass a
shallow copy of the caller's mapping. The dependency must not be able to mutate
the caller's top-level cache mapping through the adapter.

The returned entry is a context manager. Read its `payload` inside the
`with` block so cleanup occurs once on normal return and when payload access
raises. Do not wrap, replace, or suppress dependency exceptions: propagate the
same exception instance. Empty document keys and empty cache mappings are valid.

The adapter must not mutate caller inputs. No retries, network access, sleeps,
or other side effects are part of this task.
