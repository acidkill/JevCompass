"""Stable app-facing adapter (seeded with the obsolete dependency call)."""


def load_document(client, document_key, stale_ok=False, cache=None):
    # Seeded migration defect: v2 removed the old positional lookup method.
    return client.lookup(document_key, stale_ok=stale_ok, cache=cache)
