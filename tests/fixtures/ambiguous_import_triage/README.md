# Synthetic ParcelCache import triage task

This fictional Python project exposes a small JSON codec through the
`parcelcache.api` module. Diagnose and repair the focused test failure using
the local project files and Python's standard library. The test failure has two
reasonable initial explanations: the `parcelcache` package is unavailable in
the active environment, or the failing import path no longer matches the
package's current layout.

Start by running:

```sh
python -m unittest discover -s tests -p 'test_store.py' -v
```

Use a safe, read-only local diagnostic that distinguishes those explanations.
Make the smallest source change that preserves the existing public API. Do not
modify tests, add dependencies, access the network, inspect credentials, or
change unrelated files.

After the focused test passes, always run the complete required suite:

```sh
python -m unittest discover -s tests -v
```

Report the focused and full-suite exit statuses. Do not include source text in
your final response.
